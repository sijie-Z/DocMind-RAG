from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from typing import Dict, Set, Any

from fastapi import WebSocket


class NotificationConnectionManager:
    def __init__(self) -> None:
        self._connections: Dict[int, Set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()
        self._stats: Dict[str, int] = {
            "connect_total": 0,
            "disconnect_total": 0,
            "push_total": 0,
            "deliver_total": 0,
            "send_fail_total": 0,
        }

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections[user_id].add(websocket)
            self._stats["connect_total"] += 1

    def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        if user_id in self._connections and websocket in self._connections[user_id]:
            self._connections[user_id].remove(websocket)
            if not self._connections[user_id]:
                self._connections.pop(user_id, None)
            self._stats["disconnect_total"] += 1

    async def _send_to_ws(self, user_id: int, ws: WebSocket, message: str) -> bool:
        try:
            await ws.send_text(message)
            return True
        except Exception:
            self.disconnect(user_id, ws)
            self._stats["send_fail_total"] += 1
            return False

    async def push(self, user_id: int, payload: Dict[str, Any]) -> None:
        conns = list(self._connections.get(user_id, set()))
        if not conns:
            return

        self._stats["push_total"] += 1
        message = json.dumps({"type": "notification", "data": payload}, ensure_ascii=False)

        # 并发推送给所有连接，提高效率
        tasks = [self._send_to_ws(user_id, ws, message) for ws in conns]
        if tasks:
            results = await asyncio.gather(*tasks)
            self._stats["deliver_total"] += sum(1 for ok in results if ok)

    def get_stats(self) -> Dict[str, int]:
        active_connections = sum(len(conns) for conns in self._connections.values())
        return {
            **self._stats,
            "active_connections": active_connections,
            "active_users": len(self._connections),
        }


notification_ws_manager = NotificationConnectionManager()
