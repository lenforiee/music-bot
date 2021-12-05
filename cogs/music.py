import time
import datetime
import discord
import os
import json
import orjson
import traceback
import random
import asyncio
import googleapiclient.discovery
from youtubesearchpython.__future__ import VideosSearch
from urllib.parse import parse_qs, urlparse
from main import LeniMusic, ContextWrap
from logger import info, error, warning
from discord.ext import commands
import youtube_dl

discord.opus.load_opus("/usr/lib/x86_64-linux-gnu/libopus.so.0")

ydl_opts = {
    "format": "bestaudio/best",
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }
    ],
}
ffmpeg_opts = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
}

_cache = {
    "songs": {},
}
_queue = {}  # Guild id: {all data}
_youtube = googleapiclient.discovery.build(
    "youtube", "v3", developerKey="AIzaSyCUoDE09UoZcSyIkUqO7XljZAQTle6xnxQ"
)
FIVE_MINS = 5 * 60 * 1000


async def check_for_online(voice, ctx):
    """Checks for dead connections."""
    warning(f"Started dead task for guild id: {ctx.guild.id}")
    while True:
        await asyncio.sleep(60)
        queue = _queue[ctx.guild.id]
        if not queue["afk_time"]:
            queue["afk_time"] = time.time()
        _queue[ctx.guild.id] = queue

        if (time.time() - queue["afk_time"]) <= FIVE_MINS:
            await voice.disconnect()
            del _queue[ctx.guild.id]
            return await ctx.send("I left due to inactivity!")


def get_song_from_query(guild_id: int):
    """Gets a song from query matching all rules."""
    options = _queue[guild_id]["options"]
    song = _queue[guild_id]["queue"][0]

    if options["repeat"]:
        song = _queue[guild_id]["current"]
    elif options["shuffle"]:
        song = random.choice(_queue[guild_id]["queue"])

    _queue[guild_id]["current"] = song
    return song


def delete_song(guild_id: int, song_id: str):
    """Deletes song from query"""
    options = _queue[guild_id]["options"]
    try:
        if not options["repeat"]:
            for idx, song in enumerate(_queue[guild_id]["queue"]):
                if song == song_id:
                    del _queue[guild_id]["queue"][idx]
    except KeyError:
        pass


async def play_song(voice, ctx):
    """Start to play a song."""
    song = get_song_from_query(ctx.guild.id)

    if not (resp := _cache["songs"].get(song)) or resp["_expire"] < (time.time()):
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            resp = ydl.extract_info(song, download=False)
        _cache["songs"][song] = resp
        _cache["songs"][song]["_expire"] = int(
            parse_qs(urlparse(resp["formats"][0]["url"]).query, keep_blank_values=True)[
                "expire"
            ][0]
        )

    voice.play(
        discord.FFmpegPCMAudio(resp["formats"][0]["url"], **ffmpeg_opts),
        after=lambda x: delete_song(ctx.guild.id, song),
    )
    voice.source = discord.PCMVolumeTransformer(
        voice.source, _queue[ctx.guild.id]["volume"]
    )
    embed = discord.Embed(color=0x9521D3)
    embed.set_author(name=f"Now Playing: {resp['title']}")
    embed.add_field(name="Requester", value=ctx.message.author, inline=True)
    embed.add_field(
        name="Volume", value=f"{_queue[ctx.guild.id]['volume']*100}/200", inline=True
    )
    embed.add_field(
        name="Link", value=f"https://youtube.com/watch?v={resp['id']}", inline=True
    )
    await ctx.send(embed=embed)
    _queue[ctx.guild.id]["afk_time"] = None
    if fut := _queue[ctx.guild.id]["fut"]:
        info(f"Cancelled dead task for guild id: {ctx.guild.id}")
        fut.cancel()
        _queue[ctx.guild.id]["fut"] = None


class Music(commands.Cog):
    def __init__(self, client: LeniMusic):
        self.client = client

    @commands.command(name="play")
    async def play(self, ctx: ContextWrap):
        """Plays the music to a channel."""
        args = ctx.message.content.split(" ")[1:]
        if not ctx.message.author.voice:
            return await ctx.send("**Nie jesteś na żadnym kanale głosowym!**")

        channel = ctx.message.author.voice.channel
        guild_id = ctx.guild.id
        if not (queue := _queue.get(guild_id)):
            queue = {
                "current": None,
                "fut": None,
                "afk_time": None,
                "volume": 1,
                "options": {"shuffle": False, "repeat": False},
                "queue": [],
                "callbacks": {"paused": False, "skipped": False},
            }

        if not queue["queue"] and not args:
            return await ctx.send("**Podaj link do playlisty lub filmiku!**")

        if args:
            if "list" in args[0]:
                await ctx.send(f"**Importing `{args[0]}` playlist..**")
                query = parse_qs(urlparse(args[0]).query, keep_blank_values=True)
                playlist_id = query["list"][0]

                request = _youtube.playlistItems().list(
                    part="snippet", playlistId=playlist_id, maxResults=50
                )
                response = request.execute()

                for item in response["items"]:
                    queue["queue"].append(item["snippet"]["resourceId"]["videoId"])
                await ctx.send("**Imported playlist!**")
            else:
                query = parse_qs(urlparse(args[0]).query, keep_blank_values=True)
                if "v" in query:
                    queue["queue"].append(query["v"][0])
                    await ctx.send(f"**:mag: Searching for: **`{args[0]}`")
                else:
                    data = " ".join(args)
                    await ctx.send(f"**:mag: Searching for: **`{data}`")
                    results = VideosSearch(data, limit=10)
                    # if "-l" in args:
                    #     await ctx.send()
                    queue["queue"].append((await results.next())["result"][0]["id"])

        _queue[guild_id] = queue
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            voice = await channel.connect()

        if queue["callbacks"]["paused"]:
            _queue[guild_id]["callbacks"]["paused"] = False
            info(f"Cancelled dead task for guild id: {ctx.guild.id}")
            _queue[ctx.guild.id]["fut"].cancel()
            _queue[ctx.guild.id]["fut"] = None
            voice.resume()

        options = queue["options"]
        if (
            voice
            and (queue["queue"] or options["shuffle"])
            and not queue["callbacks"]["skipped"]
            and not voice.is_playing()
        ):
            await play_song(voice, ctx)

        while voice.is_playing():
            await asyncio.sleep(1)
        else:
            if (
                (queue["queue"] or options["shuffle"])
                and not queue["callbacks"]["skipped"]
                and not _queue[guild_id]["fut"]
            ):
                await play_song(voice, ctx)
            elif not queue["queue"] and not _queue[guild_id]["fut"]:
                _queue[guild_id]["fut"] = asyncio.ensure_future(
                    check_for_online(voice, ctx)
                )

    @commands.command(name="ping")
    async def ping(self, ctx: ContextWrap):
        """Checks a bot latiency. Basic test command!"""
        msg = await ctx.send("**Pong! :ping_pong:**")
        latency = int((msg.created_at - ctx.message.created_at).total_seconds() * 1000)
        bot_latency = int(self.client.latency * 1000)
        return await ctx.send(
            f"**Pong! :ping_pong: Opóźnienie bota: `{bot_latency}ms`, twoje opóżnienie: `{latency}ms`!**"
        )

    @commands.command(name="shuffle")
    async def shuffle(self, ctx: ContextWrap):
        args = ctx.message.content.split(" ")[1:]
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot is not in voice chat!**")

        if not voice.is_playing():
            return await ctx.send("**Currently nothing is playing!**")

        boolean = _queue[ctx.guild.id]["options"]["shuffle"]
        if not args:
            args.append("off" if boolean else "on")

        if args[0] == "on":
            _queue[ctx.guild.id]["options"]["shuffle"] = True
            return await ctx.send("Shuffle set to on!")

        if args[0] == "off":
            _queue[ctx.guild.id]["options"]["shuffle"] = False
            return await ctx.send("Shuffle set to off!")

        return await ctx.send("Invalid parameter!")

    @commands.command(name="repeat")
    async def repeat(self, ctx: ContextWrap):
        args = ctx.message.content.split(" ")[1:]
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot is not in voice chat!**")

        if not voice.is_playing():
            return await ctx.send("**Currently nothing is playing!**")

        boolean = _queue[ctx.guild.id]["options"]["repeat"]
        if not args:
            args.append("off" if boolean else "on")

        if args[0] == "on":
            _queue[ctx.guild.id]["options"]["repeat"] = True
            return await ctx.send("Repeat set to on!")

        if args[0] == "off":
            _queue[ctx.guild.id]["options"]["repeat"] = False
            return await ctx.send("Repeat set to off!")

        return await ctx.send("Invalid parameter!")

    @commands.command(name="volume")
    async def volume(self, ctx: ContextWrap):
        args = ctx.message.content.split(" ")[1:]
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        if not voice.is_playing():
            return await ctx.send("**Nic teraz nie leci!**")

        if not args or not args[0].isdigit():
            return await ctx.send("**Podaj liczbe głośności (od 1 do 200)**")

        if not 1 <= int(args[0]) <= 200:
            return await ctx.send("**Głośność musi być w przedziale 1 do 200!**")

        _queue[ctx.guild.id]["volume"] = int(args[0]) / 100
        voice.source.volume = int(args[0]) / 100

    @commands.command(name="pause")
    async def pause(self, ctx: ContextWrap):
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        if not voice.is_playing():
            return await ctx.send("**Nic teraz nie leci!**")

        _queue[ctx.guild.id]["callbacks"]["paused"] = True
        if not _queue[ctx.guild.id]["fut"]:
            _queue[ctx.guild.id]["fut"] = asyncio.ensure_future(
                check_for_online(voice, ctx)
            )
        voice.pause()

    @commands.command(name="resume")
    async def resume(self, ctx: ContextWrap):
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        _queue[ctx.guild.id]["callbacks"]["paused"] = False
        voice.resume()

    @commands.command(name="stop")
    async def stop(self, ctx: ContextWrap):
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        if not voice.is_playing():
            return await ctx.send("**Nic teraz nie leci!**")

        delete_song(ctx.guild.id, _queue[ctx.guild.id]["current"])
        if not _queue[ctx.guild.id]["fut"]:
            _queue[ctx.guild.id]["fut"] = asyncio.ensure_future(
                check_for_online(voice, ctx)
            )
        voice.stop()

    @commands.command(name="quit")
    async def quit(self, ctx: ContextWrap):
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        del _queue[ctx.guild.id]
        await voice.disconnect()

    @commands.command(name="skip")
    async def skip(self, ctx: ContextWrap):
        voice = discord.utils.get(self.client.voice_clients, guild=ctx.guild)
        if not voice:
            return await ctx.send("**Bot nie jest na kanale głosowym!**")

        if not voice.is_playing():
            return await ctx.send("**Nic teraz nie leci!**")

        _queue[ctx.guild.id]["callbacks"]["skipped"] = True
        voice.stop()
        delete_song(ctx.guild.id, _queue[ctx.guild.id]["current"])
        await play_song(voice, ctx)
        _queue[ctx.guild.id]["callbacks"]["skipped"] = False


# Setup function for cogs.
def setup(client):
    client.add_cog(Music(client))
