import json

from channels.generic.websocket import AsyncWebsocketConsumer

from apps.elttool.websocket.auth import (
    authenticate_ws_user_async,
    user_belongs_to_company_async,
)


class CompanyConnectionsStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.cmp_id = self.scope["url_route"]["kwargs"]["cmp_id"]
        user = await authenticate_ws_user_async(self.scope)
        if not user.is_authenticated:
            await self.close(code=4401)
            return
        if not await user_belongs_to_company_async(user, self.cmp_id):
            await self.close(code=4403)
            return
        self.scope["user"] = user
        self.group_name = f"company_connections_status_{self.cmp_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def company_connections_status_update(self, event):
        await self.send(text_data=json.dumps(event["payload"]))
