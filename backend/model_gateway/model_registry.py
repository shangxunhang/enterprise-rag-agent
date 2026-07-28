"""模型客户端注册表及其资源生命周期管理。

注册表只按稳定 ``model_name`` 保存客户端实例，不参与业务角色选择。关闭后永久拒绝
注册和读取；``release_all`` 只释放当前资源，注册表仍可继续复用。
"""

from __future__ import annotations

from threading import RLock
from typing import Dict

from contracts.base_client import BaseLLMClient


class ModelRegistry:
    """线程安全地保存模型客户端，并统一调用可选的释放/关闭钩子。"""
    def __init__(self) -> None:
        """初始化空注册表和覆盖注册、读取、释放、关闭的生命周期锁。"""
        self._clients: Dict[str, BaseLLMClient] = {}
        self._lifecycle_lock = RLock()
        self._closed = False

    def register(self, client: BaseLLMClient) -> None:
        """按 ``client.model_name`` 注册唯一客户端；重复名称或已关闭状态直接失败。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("ModelRegistry is closed")
            if client.model_name in self._clients:
                raise ValueError(f"Model client already registered: {client.model_name}")
            self._clients[client.model_name] = client

    def get(self, model_name: str) -> BaseLLMClient:
        """读取已注册客户端；未注册与注册表关闭是两类不同错误。"""
        with self._lifecycle_lock:
            if self._closed:
                raise RuntimeError("ModelRegistry is closed")
            if model_name not in self._clients:
                raise KeyError(f"Model client not found: {model_name}")
            return self._clients[model_name]

    def contains(self, model_name: str) -> bool:
        """仅在注册表仍开放且名称存在时返回真。"""
        with self._lifecycle_lock:
            return not self._closed and model_name in self._clients

    def release(self, model_name: str) -> None:
        """Release an on-demand client when it exposes a lifecycle hook."""
        with self._lifecycle_lock:
            client = self.get(model_name)
        release = getattr(client, "release", None)
        if callable(release):
            release()

    @staticmethod
    def _close_client(client: BaseLLMClient) -> None:
        """Invoke one client lifecycle hook without coupling the base port to it."""
        close = getattr(client, "close", None)
        if callable(close):
            close()
            return
        release = getattr(client, "release", None)
        if callable(release):
            release()

    def release_all(self) -> None:
        """Release every registered provider while keeping the registry reusable."""
        with self._lifecycle_lock:
            if self._closed:
                return
            clients = tuple(self._clients.values())
        for client in reversed(clients):
            try:
                self._close_client(client)
            except Exception:
                # Try every provider even when one cleanup hook fails.
                pass

    def close(self) -> None:
        """Idempotently release all providers and permanently close the registry."""
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closed = True
            clients = tuple(self._clients.values())
            self._clients.clear()
        for client in reversed(clients):
            try:
                self._close_client(client)
            except Exception:
                # Shutdown remains best-effort, but all clients get a chance.
                pass

    def names(self) -> list[str]:
        """返回当前注册名称快照，不暴露内部可变字典。"""
        with self._lifecycle_lock:
            return list(self._clients)
