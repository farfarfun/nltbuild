#!/usr/bin/python3

import json
import os
import shlex

from funshell import run_shell_list

from .base import BaseBuild
from .util import logger
from .version_sync import root_pyproject_project_version, sync_all_manifest_versions


class NpmFrontendBuild(BaseBuild):
    """基于 package.json 的前端项目构建与发布 (npm / pnpm / yarn), 支持 extbuild 子目录多包。"""

    ROOT_PACKAGE_JSON = "./package.json"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.package_json_paths: list[str] = []
        self._pkg: dict = {}
        self._pm = "npm"
        self._nltbuild_cfg: dict = {}

    @staticmethod
    def _load_json_at(path: str) -> dict:
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _nltbuild_from_pkg(pkg: dict) -> dict:
        raw = pkg.get("nltbuild")
        if isinstance(raw, dict):
            return raw
        if raw is True:
            return {"enabled": True}
        return {}

    @staticmethod
    def _package_qualifies(pkg: dict) -> bool:
        ver = pkg.get("version")
        if not isinstance(ver, str) or not ver.strip():
            return False
        cfg = NpmFrontendBuild._nltbuild_from_pkg(pkg)
        scripts = pkg.get("scripts") or {}
        custom_build = isinstance(cfg.get("build"), str) and bool(cfg["build"].strip())
        has_build = isinstance(scripts, dict) and "build" in scripts
        return bool(has_build or custom_build)

    @staticmethod
    def _collect_package_json_paths_in_root(root: str) -> list[str]:
        paths: list[str] = []
        if not os.path.isdir(root):
            return paths
        for name in sorted(os.listdir(root)):
            sub = os.path.join(root, name)
            if not os.path.isdir(sub):
                continue
            pj = os.path.join(sub, "package.json")
            if not os.path.isfile(pj):
                continue
            try:
                pkg = NpmFrontendBuild._load_json_at(pj)
                if NpmFrontendBuild._package_qualifies(pkg):
                    paths.append(pj)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"skip {pj}: {e}")
        return paths

    def _collect_package_json_paths(self) -> list[str]:
        paths: list[str] = []
        if os.path.isfile(self.ROOT_PACKAGE_JSON):
            try:
                pkg = self._load_json_at(self.ROOT_PACKAGE_JSON)
                if self._package_qualifies(pkg):
                    paths.append(self.ROOT_PACKAGE_JSON)
            except (OSError, json.JSONDecodeError) as e:
                logger.warning(f"skip root package.json: {e}")
        paths.extend(self._collect_package_json_paths_in_root("extbuild"))
        paths.extend(self._collect_package_json_paths_in_root("exts"))
        return paths

    def _detect_package_manager_for_dir(self, pkg_dir: str, cfg: dict) -> str:
        """推断包管理器: 显式配置 > packageManager 字段 > lockfile。"""
        pm = cfg.get("packageManager")
        if isinstance(pm, str):
            pm_base = pm.split("@", 1)[0].strip().lower()
            if pm_base in ("npm", "pnpm", "yarn"):
                return pm_base
        if os.path.exists(os.path.join(pkg_dir, "pnpm-lock.yaml")):
            return "pnpm"
        if os.path.exists(os.path.join(pkg_dir, "package-lock.json")):
            return "npm"
        if os.path.exists(os.path.join(pkg_dir, "yarn.lock")):
            return "yarn"
        return "npm"

    @staticmethod
    def _in_dir_shell(pkg_dir: str, inner: str) -> str:
        """用子 shell 执行, 避免 run_shell_list 用 && 串联时 cd 残留导致后续相对路径错位。"""
        d = os.path.normpath(pkg_dir)
        if d in (".", ""):
            return inner
        return f"(cd {shlex.quote(d)} && {inner})"

    def _sync_primary_state(self):
        primary = self.package_json_paths[0]
        self._pkg = self._load_json_at(primary)
        self._nltbuild_cfg = self._nltbuild_from_pkg(self._pkg)
        ver = self._pkg.get("version")
        if isinstance(ver, str) and ver.strip():
            self.version = ver.strip()
        pkg_dir = os.path.dirname(primary)
        self._pm = self._detect_package_manager_for_dir(pkg_dir, self._nltbuild_cfg)

    def check_type(self) -> bool:
        self.package_json_paths = self._collect_package_json_paths()
        if not self.package_json_paths:
            return False
        root_ver = root_pyproject_project_version()
        if root_ver is not None:
            self.version = root_ver
            primary = self.package_json_paths[0]
            self._pkg = self._load_json_at(primary)
            self._nltbuild_cfg = self._nltbuild_from_pkg(self._pkg)
            pkg_dir = os.path.dirname(primary)
            self._pm = self._detect_package_manager_for_dir(pkg_dir, self._nltbuild_cfg)
            return True
        self._sync_primary_state()
        return True

    def _write_version(self):
        for pj in self.package_json_paths:
            pkg = self._load_json_at(pj)
            pkg["version"] = self.version
            with open(pj, "w", encoding="utf-8") as f:
                json.dump(pkg, f, indent=2, ensure_ascii=False)
                f.write("\n")
        sync_all_manifest_versions(self.version)
        self._sync_primary_state()

    def _install_cmds_for_dir(self, pkg_dir: str, cfg: dict, pm: str) -> list[str]:
        custom = cfg.get("install")
        if isinstance(custom, str) and custom.strip():
            return [custom.strip()]
        if pm == "pnpm":
            lock = os.path.join(pkg_dir, "pnpm-lock.yaml")
            return ["pnpm install --frozen-lockfile" if os.path.exists(lock) else "pnpm install"]
        if pm == "yarn":
            lock = os.path.join(pkg_dir, "yarn.lock")
            return ["yarn install --frozen-lockfile" if os.path.exists(lock) else "yarn install"]
        lock = os.path.join(pkg_dir, "package-lock.json")
        return ["npm ci" if os.path.exists(lock) else "npm install"]

    def _build_cmd_for(self, cfg: dict, pm: str) -> str:
        custom = cfg.get("build")
        if isinstance(custom, str) and custom.strip():
            return custom.strip()
        if pm == "yarn":
            return "yarn run build"
        return f"{pm} run build"

    def _cmd_build(self) -> list[str]:
        out: list[str] = []
        for pj in self.package_json_paths:
            pkg_dir = os.path.dirname(pj)
            pkg = self._load_json_at(pj)
            cfg = self._nltbuild_from_pkg(pkg)
            pm = self._detect_package_manager_for_dir(pkg_dir, cfg)
            for cmd in self._install_cmds_for_dir(pkg_dir, cfg, pm):
                out.append(self._in_dir_shell(pkg_dir, cmd))
            out.append(self._in_dir_shell(pkg_dir, self._build_cmd_for(cfg, pm)))
        return out

    def install(self, *args, **kwargs):
        logger.info(f"{self.name} install (frontend dependencies)")
        if not self.package_json_paths:
            self.package_json_paths = self._collect_package_json_paths()
        if not self.package_json_paths:
            return
        cmds: list[str] = []
        for pj in self.package_json_paths:
            pkg_dir = os.path.dirname(pj)
            pkg = self._load_json_at(pj)
            cfg = self._nltbuild_from_pkg(pkg)
            pm = self._detect_package_manager_for_dir(pkg_dir, cfg)
            for c in self._install_cmds_for_dir(pkg_dir, cfg, pm):
                cmds.append(self._in_dir_shell(pkg_dir, c))
        run_shell_list(cmds)

    def _cmd_install(self) -> list[str]:
        return []

    def _publish_cmds_for_package(self, pj: str) -> list[str]:
        pkg = self._load_json_at(pj)
        cfg = self._nltbuild_from_pkg(pkg)
        pkg_dir = os.path.dirname(pj)
        pm = self._detect_package_manager_for_dir(pkg_dir, cfg)
        if cfg.get("publish") is False:
            return []
        custom = cfg.get("publish")
        if isinstance(custom, str) and custom.strip():
            return [self._in_dir_shell(pkg_dir, custom.strip())]
        if pkg.get("private") is True:
            logger.info(f"{pj}: private=true, skip publish; set nltbuild.publish to override")
            return []
        if pm == "pnpm":
            pub = "pnpm publish --no-git-checks"
        elif pm == "yarn":
            pub = "yarn npm publish"
        else:
            pub = "npm publish"
        return [self._in_dir_shell(pkg_dir, pub)]

    def _cmd_publish(self) -> list[str]:
        out: list[str] = []
        for pj in self.package_json_paths:
            out.extend(self._publish_cmds_for_package(pj))
        return out

    def _cmd_delete(self) -> list[str]:
        dirs = self._nltbuild_cfg.get("cleanDirs")
        if isinstance(dirs, list) and dirs:
            return [f"rm -rf {d}" for d in dirs if isinstance(d, str) and d.strip()]
        return [
            "rm -rf dist",
            "rm -rf build",
            "rm -rf .next",
            "rm -rf out",
            "rm -rf storybook-static",
            "rm -rf extbuild/*/dist",
            "rm -rf extbuild/*/build",
            "rm -rf extbuild/*/.next",
            "rm -rf extbuild/*/out",
            "rm -rf extbuild/*/storybook-static",
        ]
