import discord
import time
import asyncio
import aiohttp
import orjson
import socket
import random
import traceback
import os
from logger import info, error, warning
from discord.ext import commands, tasks
from typing import Optional

config = {}
if not os.path.exists("config.json"):
    with open("config.json", "w") as stream:
        stream.write("{}")
    info("Generated a new config file!")
    raise SystemExit

with open("config.json", "r") as stream:
    config = orjson.loads(stream.read())

# Kudos to cmyui for making this class.
class ContextWrap(commands.Context):
    """A lightweighted wrapper allowing to edit messages written by cmyui."""

    async def send(self, *args, **kwargs) -> Optional[discord.Message]:
        # Allows for the syntax `ctx.send('content')`
        if len(args) == 1 and isinstance(args[0], str):
            kwargs["content"] = args[0]

        # Clear previous msg params.
        kwargs["embed"] = kwargs.pop("embed", None)
        kwargs["content"] = kwargs.pop("content", None)
        kwargs["file"] = kwargs.pop("file", None)

        cached = self.bot.cache["responses"].get(self.message.id)

        if cached and (time.time() - cached["timeout"]) <= 0 and not kwargs.get("file"):
            # We have cache and it's not expired.
            msg = cached["resp"]
            await msg.edit(**kwargs)
        else:  # We either have no cached val, or it's expired.
            if kwargs.get("file") and cached:
                await cached["resp"].delete()

            msg = await super().send(**kwargs)

            self.bot.cache["responses"][self.message.id] = {
                "resp": msg,
                "timeout": int(time.time()) + 300,  # 5 min
            }

        return msg


class LeniMusic(commands.Bot):
    """Class representing a Reifike bot."""

    def __init__(self):
        super().__init__(
            command_prefix=config["prefix"],
            # help_command= None,
            auto_reconnect=True,
            owner_id=config["bot_owner"],
        )

        # A cache for responses.
        self.cache: dict = {
            "responses": {},
        }
        self.uptime: Optional[int] = None
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self._resolver: aiohttp.AsyncResolver = None
        self._http_connector = None
        self.http_client: aiohttp.ClientSession = None

    async def on_ready(self):
        """Starts up a backend tasks on bot startup."""

        delta_ms = round((time.time() - self.uptime), 2)
        delta = f"{delta_ms * 1000}ms" if delta_ms < 1 else f"{delta_ms}s"
        info(f"RedmoonMusic has started in {delta}!")

    async def on_message(self, msg: discord.Message) -> None:
        await self.wait_until_ready()

        if not msg.content or msg.author.bot:
            return
        await self.process_commands(msg)

    async def on_message_edit(
        self, before: discord.Message, after: discord.Message
    ) -> None:
        await self.wait_until_ready()

        if not after.content or after.author.bot:
            return

        if after.content == before.content:
            return
        await self.process_commands(after)

    async def on_message_delete(self, msg: discord.Message) -> None:
        cached = self.cache["responses"].get(msg.id)

        if cached:
            try:
                await cached["resp"].delete()
            except discord.NotFound:  # No 403 since it's our own message.
                pass  # Response has already been deleted.

            del self.cache["responses"][msg.id]

    async def process_commands(self, message):
        if message.author.bot:
            return

        ctx = await self.get_context(message, cls=ContextWrap)
        await self.invoke(ctx)

    @tasks.loop(seconds=30)
    async def custom_status(self) -> None:
        """Runs custom bot statuses"""
        # wait till client is connected
        await self.wait_until_ready()

        # update status
        await self.change_presence(
            activity=discord.Game(name="made by lenforiee#4663"),
            status=discord.Status.online,
        )

    def run(self, *args, **kwargs) -> None:
        async def runner():

            if not self.uptime:
                self.uptime = int(time.time())

            # Ok this is the best way you can do it so the
            # threads arent spammed we will use DNS resolution here.
            self._resolver = aiohttp.AsyncResolver()

            # Use AF_INET as its socket family to prevent HTTPS
            # related problems both locally and in production.
            self._http_connector = aiohttp.TCPConnector(
                resolver=self._resolver,
                family=socket.AF_INET,
            )

            # Client.login() will call HTTPClient.static_login() which
            # will create a session using this connector attribute.
            self.http.connector = self._http_connector

            # Now we set http session with our better connector.
            self.http_client = aiohttp.ClientSession(
                connector=self._http_connector, json_serialize=orjson.dumps
            )

            try:
                dir_list = os.listdir(f"{os.getcwd()}/cogs")
                for cog in filter(
                    lambda c: not c.startswith("__") and c.endswith(".py"), dir_list
                ):
                    cog_name = cog[:-3].replace("/", ".")
                    self.load_extension(f"cogs.{cog_name}")
                    info(f"Loaded '{cog_name}'.py to bot class!")
            except Exception:
                error(f"Could not load all extensions! Error: {traceback.print_exc()}")
                raise SystemExit

            # Start a bot loop.
            try:
                await self.start(config["token"], *args, **kwargs)
            finally:
                # Close all connections.
                await self.http_client.close()

                # Close all http connections.
                await self._http_connector.close()
                await self._resolver.close()

                # Close bot it self.
                self.close()

        # Set tasks.
        self.loop.create_task(runner())
        try:
            # Start custom status task.
            self.custom_status.start()
            self.loop.run_forever()
        finally:
            self.custom_status.cancel()


if __name__ == "__main__":
    bot = LeniMusic()
    bot.run()
