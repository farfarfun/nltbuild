#!/usr/bin/python3

import shlex

from funshell import run_shell, run_shell_list

from .util import logger, opencommit_commit


class BaseBuild:
    """构建工具的基类"""

    def __init__(self, name=None):
        self.repo_path = run_shell("git rev-parse --show-toplevel", printf=False)
        self.name = name or self.repo_path.split("/")[-1]
        self.version = None

    def check_type(self) -> bool:
        """检查是否为当前构建类型"""
        raise NotImplementedError

    def _write_version(self):
        """写入版本号"""
        raise NotImplementedError

    def __version_upgrade(self, step=128):
        """版本号自增"""
        version = self.version
        if version is None:
            version = "0.0.1"

        version1 = [int(i) for i in version.split(".")]
        version2 = version1[0] * step * step + version1[1] * step + version1[2] + 1

        version1[2] = version2 % step
        version1[1] = int(version2 / step) % step
        version1[0] = int(version2 / step / step)

        return "{}.{}.{}".format(*version1)

    def _cmd_build(self) -> list[str]:
        """构建命令"""
        return []

    def _cmd_publish(self) -> list[str]:
        """发布命令"""
        return []

    def _cmd_install(self) -> list[str]:
        """安装命令"""
        return ["pip install dist/*.whl --force-reinstall"]

    def _cmd_delete(self) -> list[str]:
        """清理命令"""
        return [
            "rm -rf dist",
            "rm -rf extbuild/*/dist",
            "rm -rf build",
            "rm -rf extbuild/*/build",
            "rm -rf *.egg-info",
            "rm -rf extbuild/*/src/*.egg-info",
            "rm -rf uv.lock",
        ]

    def upgrade(self, *args, **kwargs):
        """升级版本"""
        self.version = self.__version_upgrade()
        self._write_version()

    def pull(self, *args, **kwargs):
        """拉取代码"""
        logger.info(f"{self.name} pull")
        run_shell_list(["git pull"])

    def push(self, message="add", *args, **kwargs):
        """推送代码"""
        logger.info(f"{self.name} push")
        run_shell_list(["git add -A"])
        try:
            if not opencommit_commit(message):
                run_shell_list([f"git commit -m {shlex.quote(message)}"])
        except Exception as e:
            logger.warning(f"commit skipped or failed: {e}")
        run_shell_list(["git push"])

    def install(self, *args, **kwargs):
        """安装包"""
        logger.info(f"{self.name} install")
        run_shell_list(self._cmd_build() + self._cmd_install() + self._cmd_delete())

    def build(self, message="add", *args, **kwargs):
        """构建发布流程"""
        logger.info(f"{self.name} build")
        self.pull()
        self.upgrade()
        run_shell_list(
            self._cmd_delete() + self._cmd_build() + self._cmd_install() + self._cmd_publish() + self._cmd_delete()
        )
        self.push(message=message)
        self.tags()

    def clean_history(self, *args, **kwargs):
        """清理git历史记录"""
        logger.info(f"{self.name} clean history")
        current_branch = run_shell("git rev-parse --abbrev-ref HEAD", printf=False).strip() or "master"
        run_shell_list(
            [
                "git tag -d $(git tag -l) || true",
                "git fetch",
                "git push origin --delete $(git tag -l)",
                "git tag -d $(git tag -l) || true",
                "git checkout --orphan latest_branch",
                "git add -A",
                'git commit -am "clear history"',
                f"git branch -D {current_branch} || true",
                f"git branch -m {current_branch}",
                f"git push -f origin {current_branch}",
                f"git push --set-upstream origin {current_branch}",
                f"echo {self.name} success",
            ]
        )

    def clean(self, *args, **kwargs):
        """清理git缓存"""
        logger.info(f"{self.name} clean")
        run_shell_list(
            [
                "git rm -r --cached .",
                "git add .",
                "git commit -m 'update .gitignore' || true",
                "git gc --aggressive",
            ]
        )

    def tags(self, *args, **kwargs):
        """创建版本标签"""
        if not self.version:
            logger.warning("skip tags: version is not set")
            return
        run_shell_list(
            [
                f"git tag --force v{self.version}",
                "git push --tags",
            ]
        )
