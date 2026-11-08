import json

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.elttool.websocket.auth import (
    authenticate_ws_user_async,
    user_owns_connection_async,
)


class ConnectionActivityLogConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.connection_id = self.scope["url_route"]["kwargs"]["connection_id"]
        user = await authenticate_ws_user_async(self.scope)
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await user_owns_connection_async(user, self.connection_id):
            await self.close(code=4403)
            return
        self.scope["user"] = user
        self.group_name = f"connection_activity_log_{self.connection_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def connection_activity_log_update(self, event):
        await self.send(text_data=json.dumps(event["payload"]))
