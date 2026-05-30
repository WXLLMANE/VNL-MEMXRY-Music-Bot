#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import discord
from discord.ext import commands
from discord import app_commands
import yt_dlp as youtube_dl
import asyncio
import re
import json
import random
import time
import os
import logging
import aiosqlite
import aiohttp
from aiohttp_socks import ProxyConnector

# ==================================================
# ================= ВАШИ ДАННЫЕ ===================
# ==================================================
DISCORD_TOKEN = "MTUwMzA0MzQ5MjM4ODI3ODMyMg.GFU7ze.ZPPzhp7ptibgdOm771KsUTJCp2xY-J63A1O4mc"
FFMPEG_PATH = r"C:\Users\sereg\Desktop\VNL.MEMXRY Music Bot\ffmpeg-2026-05-06-git-f2e5eff3ff-full_build\bin\ffmpeg.exe"
PROXY_URL = None

VK_COOKIES_PATH = r"C:\Users\sereg\Desktop\VNL.MEMXRY Music Bot\vk_cookies.txt"

# ==================================================
# ================= НАСТРОЙКИ =====================
# ==================================================
LOOP_MODES = {}
RADIO_MODE = {}
HISTORY = {}
QUEUE_BACKUP_FILE = "queue_backup.json"

logging.basicConfig(
    filename='bot.log',
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger('music_bot')

queues = {}
player_controllers = {}
volume_levels = {}

# Настройки yt-dlp
ydl_opts = {
    'quiet': True,
    'no_warnings': True,
    'extract_flat': False,
    'source_address': '0.0.0.0',
    'default_search': 'ytsearch',
}
if VK_COOKIES_PATH and os.path.exists(VK_COOKIES_PATH):
    ydl_opts['cookiefile'] = VK_COOKIES_PATH
    logger.info("Загружены cookies для VK")

ffmpeg_options = {
    'options': '-vn',
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'executable': FFMPEG_PATH
}

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
bot = commands.Bot(command_prefix='!', intents=intents)
tree = bot.tree

# ==================================================
# ================= ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ========
# ==================================================
def get_queue(guild_id):
    if guild_id not in queues:
        queues[guild_id] = asyncio.Queue()
    return queues[guild_id]

def cleanup_guild(guild_id):
    if guild_id in queues:
        while not queues[guild_id].empty():
            try:
                queues[guild_id].get_nowait()
            except:
                break
        queues[guild_id] = asyncio.Queue()
    if guild_id in player_controllers:
        asyncio.create_task(player_controllers[guild_id].cleanup())
        player_controllers.pop(guild_id, None)

def shuffle_queue(guild_id):
    queue = get_queue(guild_id)
    items = []
    while not queue.empty():
        items.append(queue.get_nowait())
    random.shuffle(items)
    for item in items:
        queue.put_nowait(item)

async def save_queue_backup(guild_id):
    queue = get_queue(guild_id)
    items = []
    temp = []
    while not queue.empty():
        item = await queue.get()
        items.append(item)
        temp.append(item)
    for item in temp:
        await queue.put(item)
    if items:
        data = {"guild_id": guild_id, "queue": items}
        try:
            if os.path.exists(QUEUE_BACKUP_FILE):
                with open(QUEUE_BACKUP_FILE, "r", encoding="utf-8") as f:
                    all_data = json.load(f)
            else:
                all_data = []
            all_data = [entry for entry in all_data if entry.get("guild_id") != guild_id]
            all_data.append(data)
            with open(QUEUE_BACKUP_FILE, "w", encoding="utf-8") as f:
                json.dump(all_data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"Ошибка сохранения очереди: {e}")

async def restore_queues():
    if not os.path.exists(QUEUE_BACKUP_FILE):
        return
    try:
        with open(QUEUE_BACKUP_FILE, "r", encoding="utf-8") as f:
            all_data = json.load(f)
        for entry in all_data:
            guild_id = entry.get("guild_id")
            items = entry.get("queue", [])
            if guild_id and items:
                queue = get_queue(guild_id)
                for item in items:
                    await queue.put(item)
                logger.info(f"Восстановлено {len(items)} треков для гильдии {guild_id}")
    except Exception as e:
        logger.error(f"Ошибка восстановления очереди: {e}")

async def get_audio_url(video_url, retries=3):
    for attempt in range(retries):
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                if 'formats' in info:
                    for f in info['formats']:
                        if f.get('acodec') != 'none' and f.get('vcodec') == 'none':
                            return f['url'], info.get('duration', 0), info.get('thumbnail')
                    for f in info['formats']:
                        if f.get('acodec') != 'none':
                            return f['url'], info.get('duration', 0), info.get('thumbnail')
                if 'url' in info:
                    return info['url'], info.get('duration', 0), info.get('thumbnail')
        except Exception as e:
            logger.warning(f"Попытка {attempt+1} не удалась: {e}")
            await asyncio.sleep(2)
    return None, 0, None

async def search_youtube(query, max_results=5):
    ydl_search = ydl_opts.copy()
    ydl_search['extract_flat'] = True
    try:
        with youtube_dl.YoutubeDL(ydl_search) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(f"ytsearch{max_results}:{query}", download=False))
            if 'entries' in info and info['entries']:
                return info['entries']
    except Exception as e:
        logger.error(f"YouTube поиск ошибка: {e}")
    return []

async def search_youtube_playlist(query, max_results=1):
    ydl_playlist = ydl_opts.copy()
    ydl_playlist['extract_flat'] = False
    ydl_playlist['quiet'] = True
    try:
        with youtube_dl.YoutubeDL(ydl_playlist) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(
                None, lambda: ydl.extract_info(f"ytsearch{max_results}:{query} playlist", download=False)
            )
            if 'entries' in info and info['entries']:
                first_entry = info['entries'][0]
                if 'entries' in first_entry:
                    return first_entry['entries']
                playlist_info = await asyncio.get_event_loop().run_in_executor(
                    None, lambda: ydl.extract_info(f"ytsearch{max_results}:{query}+playlist", download=False)
                )
                if 'entries' in playlist_info and playlist_info['entries']:
                    return playlist_info['entries']
    except Exception as e:
        logger.error(f"YouTube плейлист ошибка: {e}")
    return []

async def extract_vk_playlist(url: str):
    opts = ydl_opts.copy()
    opts['extract_flat'] = True
    opts['quiet'] = True
    try:
        with youtube_dl.YoutubeDL(opts) as ydl:
            info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(url, download=False))
            if 'entries' in info:
                return info['entries']
            else:
                return [info] if info else []
    except Exception as e:
        logger.error(f"Ошибка извлечения VK: {e}")
        return []

async def spogo_playlist_items(playlist_id: str, limit=50):
    import shutil
    spogo_path = shutil.which('spogo')
    if not spogo_path:
        return []
    cmd = [spogo_path, "playlist", "tracks", playlist_id, "--json", "--limit", str(limit)]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode().strip()
        if not stdout_str:
            return []
        data = json.loads(stdout_str)
        items = data.get("items") if isinstance(data, dict) else data
        if not items:
            return []
        tracks = []
        for item in items:
            track = item.get("track") if isinstance(item, dict) and "track" in item else item
            if track and isinstance(track, dict):
                name = track.get("name")
                artists = track.get("artists")
                if artists and isinstance(artists, list):
                    artist = artists[0] if isinstance(artists[0], str) else artists[0].get("name")
                else:
                    artist = "Unknown"
                if name and artist:
                    tracks.append({"artist": artist, "title": name})
        return tracks
    except Exception as e:
        print(f"[spogo] Ошибка: {e}")
        return []

async def spogo_info_track(track_id: str):
    import shutil
    spogo_path = shutil.which('spogo')
    if not spogo_path:
        return None
    cmd = [spogo_path, "track", "info", track_id, "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode().strip()
        if not stdout_str:
            return None
        data = json.loads(stdout_str)
        artist = None
        if "artists" in data and data["artists"]:
            artist = data["artists"][0] if isinstance(data["artists"][0], str) else data["artists"][0].get("name")
        title = data.get("name")
        thumbnail = None
        if "album" in data and "images" in data["album"] and data["album"]["images"]:
            thumbnail = data["album"]["images"][0].get("url")
        if artist and title:
            return {"artist": artist, "title": title, "thumbnail": thumbnail}
        return None
    except Exception as e:
        print(f"[spogo] Ошибка получения трека: {e}")
        return None

async def spogo_search_track(query: str):
    import shutil
    spogo_path = shutil.which('spogo')
    if not spogo_path:
        return None
    cmd = [spogo_path, "search", "track", query, "--limit", "1", "--json"]
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await proc.communicate()
        stdout_str = stdout.decode().strip()
        if not stdout_str:
            return None
        data = json.loads(stdout_str)
        if "items" in data and data["items"]:
            track = data["items"][0]
            artist = None
            if "artists" in track and track["artists"]:
                artist = track["artists"][0] if isinstance(track["artists"][0], str) else track["artists"][0].get("name")
            title = track.get("name")
            if artist and title:
                return {"artist": artist, "title": title, "thumbnail": None}
        return None
    except Exception as e:
        print(f"[spogo] Ошибка поиска: {e}")
        return None

# ================= ФУНКЦИИ ВОСПРОИЗВЕДЕНИЯ ========
async def force_stop_voice(vc):
    if vc and vc.is_playing():
        vc.stop()
        while vc.is_playing():
            await asyncio.sleep(0.1)
        await asyncio.sleep(0.3)

async def play_song(ctx, vc, url, title, artist, duration, source, requester, thumbnail=None):
    if not url:
        await ctx.send("❌ Не удалось получить ссылку на аудио.")
        return

    queue = get_queue(ctx.guild.id)
    if vc and vc.is_playing():
        await queue.put({
            'url': url,
            'title': f"{title} — {artist}",
            'artist': artist,
            'duration': duration,
            'requester': requester,
            'thumbnail': thumbnail
        })
        await save_queue_backup(ctx.guild.id)
        hist = HISTORY.setdefault(ctx.guild.id, [])
        hist.append({'title': title, 'artist': artist, 'url': url, 'requester': requester})
        if len(hist) > 20:
            hist.pop(0)
        return

    if vc and (vc.is_playing() or vc.is_paused()):
        await force_stop_voice(vc)

    try:
        audio = discord.FFmpegPCMAudio(url, **ffmpeg_options)
        vol = volume_levels.get(ctx.guild.id, 50) / 100
        audio = discord.PCMVolumeTransformer(audio, volume=vol)
        vc.play(audio, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, ctx.guild.id), bot.loop))
    except Exception as e:
        logger.error(f"Ошибка воспроизведения: {e}")
        await ctx.send(f"⚠️ Ошибка воспроизведения: {e}")
        return

    hist = HISTORY.setdefault(ctx.guild.id, [])
    hist.append({'title': title, 'artist': artist, 'url': url, 'requester': requester})
    if len(hist) > 20:
        hist.pop(0)

    queue_len = queue.qsize()
    current_vol = volume_levels.get(ctx.guild.id, 50)
    embed = discord.Embed(description="Загрузка...", color=discord.Color.blue())
    view = MusicControlButtons(ctx, None)
    msg = await ctx.send(embed=embed, view=view)
    controller = PlayerController(ctx, msg, title, artist, duration, queue_len, requester, source, current_vol, thumbnail)
    view.controller = controller
    if ctx.guild.id in player_controllers:
        await player_controllers[ctx.guild.id].cleanup()
    player_controllers[ctx.guild.id] = controller
    await controller.update_embed()
    await controller.start_updates()
    await save_queue_backup(ctx.guild.id)

async def play_next(ctx, guild_id):
    queue = get_queue(guild_id)
    vc = ctx.voice_client
    if not vc or not vc.is_connected():
        return

    await force_stop_voice(vc)

    if queue.empty():
        if RADIO_MODE.get(guild_id, False) and HISTORY.get(guild_id):
            last_track = HISTORY[guild_id][-1]
            query = f"{last_track['artist']} - {last_track['title']} radio"
            entries = await search_youtube(query)
            if entries:
                entry = entries[0]
                video_url = f"https://youtube.com/watch?v={entry['id']}"
                audio_url, duration, thumb = await get_audio_url(video_url)
                if audio_url:
                    title = entry.get('title', 'Неизвестный трек')
                    parts = title.split(' - ', 1)
                    artist_disp = parts[0] if len(parts) == 2 else "Unknown"
                    title_disp = parts[1] if len(parts) == 2 else title
                    await queue.put({'url': audio_url, 'title': f"{title_disp} — {artist_disp}", 'artist': artist_disp, 'duration': duration, 'requester': ctx.author, 'thumbnail': thumb})
                    logger.info(f"Радио: добавлен похожий трек {title_disp} — {artist_disp}")
        await asyncio.sleep(60)
        if queue.empty() and ctx.voice_client and ctx.voice_client.is_connected():
            await ctx.voice_client.disconnect()
            cleanup_guild(guild_id)
        return

    next_song = await queue.get()
    audio_url = next_song['url']
    title_artist = next_song['title']
    parts = title_artist.split(' — ')
    title = parts[0] if len(parts) > 0 else "Неизвестный трек"
    artist = parts[1] if len(parts) > 1 else "Неизвестный исполнитель"
    duration = next_song.get('duration', 0)
    requester = next_song.get('requester', ctx.author)
    thumbnail = next_song.get('thumbnail')

    try:
        audio = discord.FFmpegPCMAudio(audio_url, **ffmpeg_options)
        vol = volume_levels.get(ctx.guild.id, 50) / 100
        audio = discord.PCMVolumeTransformer(audio, volume=vol)
        vc.play(audio, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, ctx.guild.id), bot.loop))
    except Exception as e:
        logger.error(f"PlayNext ошибка: {e}")
        await ctx.send(f"⚠️ Ошибка воспроизведения следующего трека: {e}")
        await play_next(ctx, guild_id)
        return

    queue_len = queue.qsize()
    current_vol = volume_levels.get(ctx.guild.id, 50)
    source = "youtube"
    embed = discord.Embed(description="Загрузка...", color=discord.Color.blue())
    view = MusicControlButtons(ctx, None)
    msg = await ctx.send(embed=embed, view=view)
    controller = PlayerController(ctx, msg, title, artist, duration, queue_len, requester, source, current_vol, thumbnail)
    view.controller = controller
    if guild_id in player_controllers:
        await player_controllers[guild_id].cleanup()
    player_controllers[guild_id] = controller
    await controller.update_embed()
    await controller.start_updates()
    await save_queue_backup(guild_id)

# ================= КЛАССЫ ИНТЕРФЕЙСА =============
class PlayerController:
    def __init__(self, ctx, message, title, artist, duration, queue_len, requester, source, volume, thumbnail=None):
        self.ctx = ctx
        self.message = message
        self.title = title
        self.artist = artist
        self.duration = duration
        self.requester = requester
        self.source = source
        self.volume = volume
        self.thumbnail = thumbnail
        self.update_task = None
        self.start_time = None

    async def start_updates(self):
        self.update_task = asyncio.create_task(self.update_loop())

    async def update_loop(self):
        while True:
            await asyncio.sleep(1)
            if self.ctx.voice_client and (self.ctx.voice_client.is_playing() or self.ctx.voice_client.is_paused()):
                await self.update_embed()
            else:
                break
        self.update_task = None

    async def update_embed(self):
        embed = self.create_embed()
        await self.message.edit(embed=embed)

    def create_embed(self):
        queue_len = get_queue(self.ctx.guild.id).qsize()
        duration_str = f"{self.duration // 60:02d}:{self.duration % 60:02d}" if self.duration else "00:00"
        current_pos = 0
        if self.ctx.voice_client and self.ctx.voice_client.source:
            if hasattr(self.ctx.voice_client.source, '_position'):
                current_pos = self.ctx.voice_client.source._position // 1000
            elif self.start_time is None:
                self.start_time = time.time()
            else:
                current_pos = int(time.time() - self.start_time)
        else:
            self.start_time = None

        if self.duration > 0:
            progress_ratio = min(current_pos / self.duration, 1.0)
            bar_len = 20
            filled = int(bar_len * progress_ratio)
            bar = '█' * filled + '░' * (bar_len - filled)
            progress_text = f"{bar} {self.format_duration(current_pos)} / {duration_str}\n\n"
        else:
            progress_text = ""

        description = (
            f"# Музыкальный плеер\n\n"
            f"**{self.title} — {self.artist}**\n\n"
            f"- Длительность: {duration_str}\n"
            f"- В очереди: {queue_len}\n"
            f"- Добавил: {self.requester.display_name}\n\n"
            f"---\n\n"
            f"{progress_text}"
            f"{self.title.lower()}\n\n"
            f"Источник: {self.source} | Громкость: {self.volume}%\n\n"
            f"---"
        )
        embed = discord.Embed(description=description, color=discord.Color.blue())
        if self.thumbnail:
            embed.set_thumbnail(url=self.thumbnail)
        return embed

    def format_duration(self, seconds):
        m, s = divmod(int(seconds), 60)
        return f"{m:02d}:{s:02d}"

    async def cleanup(self):
        if self.update_task:
            self.update_task.cancel()
            self.update_task = None
        try:
            await self.message.delete()
        except:
            pass

class MusicControlButtons(discord.ui.View):
    def __init__(self, ctx, controller):
        super().__init__(timeout=None)
        self.ctx = ctx
        self.controller = controller

    async def interaction_check(self, interaction):
        return interaction.user == self.ctx.author

    @discord.ui.button(label="⏯️", style=discord.ButtonStyle.green, emoji="⏯️")
    async def play_pause(self, interaction, button):
        vc = self.ctx.voice_client
        if vc and vc.is_connected():
            if vc.is_playing():
                vc.pause()
                await interaction.response.send_message("⏸", ephemeral=True)
            elif vc.is_paused():
                vc.resume()
                await interaction.response.send_message("▶", ephemeral=True)
        await self.controller.update_embed()

    @discord.ui.button(label="⏹️", style=discord.ButtonStyle.red, emoji="⏹️")
    async def stop(self, interaction, button):
        vc = self.ctx.voice_client
        if vc and vc.is_connected():
            vc.stop()
            cleanup_guild(self.ctx.guild.id)
            await vc.disconnect()
            await interaction.response.send_message("⏹️", ephemeral=True)
        await self.controller.cleanup()

    @discord.ui.button(label="⏩", style=discord.ButtonStyle.blurple, emoji="⏩")
    async def skip(self, interaction, button):
        vc = self.ctx.voice_client
        if vc and vc.is_playing():
            vc.stop()
            await force_stop_voice(vc)
            await interaction.response.send_message("⏩", ephemeral=True)
        await self.controller.update_embed()

    @discord.ui.button(label="📋", style=discord.ButtonStyle.gray, emoji="📋")
    async def show_queue(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        q = get_queue(self.ctx.guild.id)
        if q.empty():
            await interaction.followup.send("Очередь пуста.", ephemeral=True)
            return
        items = []
        temp = []
        while not q.empty():
            item = await q.get()
            items.append(item['title'])
            temp.append(item)
        for item in temp:
            await q.put(item)
        out = "\n".join(f"{i+1}. {t}" for i, t in enumerate(items[:10]))
        await interaction.followup.send(f"**Очередь:**\n{out}", ephemeral=True)

    @discord.ui.button(label="🔊", style=discord.ButtonStyle.green, emoji="🔊")
    async def vol_up(self, interaction, button):
        try:
            new = volume_levels.get(self.ctx.guild.id, 50) + 10
            if new > 100:
                new = 100
            volume_levels[self.ctx.guild.id] = new
            vc = self.ctx.voice_client
            if vc and vc.source and hasattr(vc.source, 'volume'):
                vc.source.volume = new / 100
            self.controller.volume = new
            await self.controller.update_embed()
            await interaction.response.send_message(f"🔊 Громкость: {new}%", ephemeral=True)
        except discord.errors.NotFound:
            pass

    @discord.ui.button(label="🔉", style=discord.ButtonStyle.red, emoji="🔉")
    async def vol_down(self, interaction, button):
        try:
            new = volume_levels.get(self.ctx.guild.id, 50) - 10
            if new < 0:
                new = 0
            volume_levels[self.ctx.guild.id] = new
            vc = self.ctx.voice_client
            if vc and vc.source and hasattr(vc.source, 'volume'):
                vc.source.volume = new / 100
            self.controller.volume = new
            await self.controller.update_embed()
            await interaction.response.send_message(f"🔉 Громкость: {new}%", ephemeral=True)
        except discord.errors.NotFound:
            pass

    @discord.ui.button(label="🔄", style=discord.ButtonStyle.blurple, emoji="🔄")
    async def loop(self, interaction, button):
        mode = LOOP_MODES.get(self.ctx.guild.id, 'off')
        if mode == 'off':
            LOOP_MODES[self.ctx.guild.id] = 'queue'
            await interaction.response.send_message("🔁 Повтор очереди включён", ephemeral=True)
        elif mode == 'queue':
            LOOP_MODES[self.ctx.guild.id] = 'single'
            await interaction.response.send_message("🔂 Повтор трека включён", ephemeral=True)
        else:
            LOOP_MODES[self.ctx.guild.id] = 'off'
            await interaction.response.send_message("🔁 Повтор выключен", ephemeral=True)
        await self.controller.update_embed()

    @discord.ui.button(label="🔀", style=discord.ButtonStyle.blurple, emoji="🔀")
    async def shuffle(self, interaction, button):
        shuffle_queue(self.ctx.guild.id)
        await interaction.response.send_message("🔀 Очередь перемешана", ephemeral=True)
        await self.controller.update_embed()

    @discord.ui.button(label="↩️", style=discord.ButtonStyle.blurple, emoji="↩️")
    async def back(self, interaction, button):
        hist = HISTORY.get(self.ctx.guild.id, [])
        if len(hist) < 2:
            await interaction.response.send_message("❌ Нет предыдущего трека.", ephemeral=True)
            return
        prev_track = hist[-2]
        queue = get_queue(self.ctx.guild.id)
        await queue.put({
            'url': prev_track['url'],
            'title': f"{prev_track['title']} — {prev_track['artist']}",
            'artist': prev_track['artist'],
            'duration': 0,
            'requester': prev_track['requester']
        })
        vc = self.ctx.voice_client
        if vc and vc.is_playing():
            await force_stop_voice(vc)
        await interaction.response.send_message("↩️ Возврат к предыдущему треку", ephemeral=True)
        await self.controller.update_embed()

# ================= КОМАНДЫ ========================
@bot.command(name='play')
async def play(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ Вы не в голосовом канале!")
        return
    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)

    # --- YouTube плейлист ---
    playlist_pattern = r'(?:youtube\.com/playlist\?list=|youtu\.be/.*\?list=)([a-zA-Z0-9_-]+)'
    playlist_match = re.search(playlist_pattern, query)
    if playlist_match:
        await ctx.send("🔄 Обработка плейлиста YouTube...")
        ydl_opts_playlist = ydl_opts.copy()
        ydl_opts_playlist['extract_flat'] = False
        ydl_opts_playlist['quiet'] = True
        try:
            with youtube_dl.YoutubeDL(ydl_opts_playlist) as ydl:
                info = await asyncio.get_event_loop().run_in_executor(None, lambda: ydl.extract_info(query, download=False))
                entries = info.get('entries', [])[:50]
                if not entries:
                    await ctx.send("❌ Плейлист пуст.")
                    return
                total = len(entries)
                await ctx.send(f"📋 Найдено треков: {total}. Добавляю в очередь...")
                added = 0
                for entry in entries:
                    video_url = f"https://youtube.com/watch?v={entry['id']}"
                    title = entry.get('title', 'Неизвестный трек')
                    audio_url, duration, thumb = await get_audio_url(video_url)
                    if audio_url:
                        parts = title.split(' - ', 1)
                        artist_disp = parts[0] if len(parts) == 2 else "Unknown"
                        title_disp = parts[1] if len(parts) == 2 else title
                        await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "YouTube (плейлист)", ctx.author, thumb)
                        added += 1
                        await asyncio.sleep(0.3)
                    else:
                        await ctx.send(f"⚠️ Не удалось извлечь аудио для `{title}`, пропускаю.")
                await ctx.send(f"✅ Добавлено **{added}** из {total} треков.")
                return
        except Exception as e:
            await ctx.send(f"⚠️ Ошибка: {e}")
            return

    # --- Плейлист Spotify ---
    if 'open.spotify.com/playlist/' in query:
        await ctx.send("🔄 Обработка плейлиста Spotify...")
        playlist_id = query.split('/')[-1].split('?')[0]
        tracks = await spogo_playlist_items(playlist_id, limit=50)
        if not tracks:
            await ctx.send("❌ Не удалось получить треки плейлиста Spotify.")
            return
        total = len(tracks)
        await ctx.send(f"📋 Найдено треков в плейлисте: {total}. Добавляю в очередь...")
        added = 0
        for track in tracks:
            artist = track['artist']
            title = track['title']
            search_query = f"{artist} - {title}"
            entries = await search_youtube(search_query)
            if not entries:
                await ctx.send(f"⚠️ Не удалось найти трек `{artist} - {title}` на YouTube, пропускаю.")
                continue
            for entry in entries:
                video_url = f"https://youtube.com/watch?v={entry['id']}"
                audio_url, duration, thumb = await get_audio_url(video_url)
                if audio_url:
                    await play_song(ctx, vc, audio_url, title, artist, duration, "Spotify плейлист + YouTube", ctx.author, thumb)
                    added += 1
                    await asyncio.sleep(0.3)
                    break
                else:
                    await ctx.send(f"⚠️ Трек `{title}` не удалось извлечь, пробую следующий...")
        await ctx.send(f"✅ Добавлено **{added}** из {total} треков.")
        return

    # --- Одиночный трек Spotify ---
    if 'open.spotify.com/track/' in query:
        await ctx.send("🔍 Обрабатываю ссылку Spotify...")
        track_id = query.split('/')[-1].split('?')[0]
        track_info = await spogo_info_track(track_id)
        if track_info and track_info.get('artist') and track_info.get('title'):
            search_query = f"{track_info['artist']} - {track_info['title']}"
            await ctx.send(f"✨ Найдено на Spotify: **{track_info['artist']} - {track_info['title']}**\n🔎 Ищу на YouTube...")
            entries = await search_youtube(search_query)
            if not entries:
                await ctx.send("❌ Не удалось найти трек на YouTube.")
                return
            for entry in entries:
                video_url = f"https://youtube.com/watch?v={entry['id']}"
                title = entry.get('title', 'Неизвестный трек')
                parts = title.split(' - ', 1)
                artist_disp = parts[0] if len(parts) == 2 else "Unknown"
                title_disp = parts[1] if len(parts) == 2 else title
                audio_url, duration, thumb = await get_audio_url(video_url)
                if audio_url:
                    await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "Spotify + YouTube", ctx.author, thumb)
                    return
                else:
                    await ctx.send(f"⚠️ Трек `{title}` не удалось извлечь, пробую следующий...")
            await ctx.send("❌ Не удалось найти работающий трек на YouTube.")
            return
        else:
            await ctx.send("❌ Не удалось распознать трек Spotify.")
            return

    # --- Одиночный YouTube ---
    youtube_pattern = r'(?:youtube\.com/watch\?v=|youtu\.be/)([a-zA-Z0-9_-]+)'
    youtube_match = re.search(youtube_pattern, query)
    if youtube_match:
        video_id = youtube_match.group(1)
        video_url = f"https://youtube.com/watch?v={video_id}"
        await ctx.send("🔍 Обрабатываю ссылку YouTube...")
        audio_url, duration, thumb = await get_audio_url(video_url)
        if not audio_url:
            await ctx.send("❌ Не удалось извлечь аудио из видео.")
            return
        try:
            with youtube_dl.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(video_url, download=False)
                title = info.get('title', 'Неизвестный трек')
                if not thumb:
                    thumb = info.get('thumbnail')
        except:
            title = "Неизвестный трек"
        parts = title.split(' - ', 1)
        artist_disp = parts[0] if len(parts) == 2 else "Unknown"
        title_disp = parts[1] if len(parts) == 2 else title
        await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "YouTube", ctx.author, thumb)
        return

    # --- Текстовый запрос (Spotify + YouTube) ---
    if not re.match(r'https?://', query):
        await ctx.send("🔍 Ищу на Spotify...")
        track_info = await spogo_search_track(query)
        if track_info and track_info.get('artist') and track_info.get('title'):
            search_query = f"{track_info['artist']} - {track_info['title']}"
            await ctx.send(f"✨ Найдено на Spotify: **{track_info['artist']} - {track_info['title']}**\n🔎 Ищу на YouTube...")
            entries = await search_youtube(search_query)
            if not entries:
                await ctx.send("❌ Не удалось найти трек на YouTube.")
                return
            for entry in entries:
                video_url = f"https://youtube.com/watch?v={entry['id']}"
                title = entry.get('title', 'Неизвестный трек')
                parts = title.split(' - ', 1)
                artist_disp = parts[0] if len(parts) == 2 else "Unknown"
                title_disp = parts[1] if len(parts) == 2 else title
                audio_url, duration, thumb = await get_audio_url(video_url)
                if audio_url:
                    await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "Spotify + YouTube", ctx.author, thumb)
                    return
                else:
                    await ctx.send(f"⚠️ Трек `{title}` не удалось извлечь, пробую следующий...")
            await ctx.send("❌ Не удалось найти работающий трек на YouTube.")
            return
        else:
            await ctx.send("💿 Не найдено на Spotify, ищу на YouTube...")

    # --- Запасной вариант: поиск на YouTube ---
    entries = await search_youtube(query)
    if not entries:
        await ctx.send("❌ Ничего не найдено на YouTube.")
        return
    for entry in entries:
        video_url = f"https://youtube.com/watch?v={entry['id']}"
        title = entry.get('title', 'Неизвестный трек')
        parts = title.split(' - ', 1)
        artist_disp = parts[0] if len(parts) == 2 else "Unknown"
        title_disp = parts[1] if len(parts) == 2 else title
        audio_url, duration, thumb = await get_audio_url(video_url)
        if audio_url:
            await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "YouTube", ctx.author, thumb)
            return
        else:
            await ctx.send(f"⚠️ Не удалось извлечь звук из `{title}`, пробую следующий...")
    await ctx.send("❌ Все найденные варианты недоступны для извлечения аудио.")

# ================= НОВЫЕ КОМАНДЫ ДЛЯ playyt и playlistyt =================
@bot.command(name='playyt')
async def playyt(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ Вы не в голосовом канале!")
        return
    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)
    entries = await search_youtube(query)
    if not entries:
        await ctx.send("❌ Ничего не найдено на YouTube.")
        return
    for entry in entries:
        video_url = f"https://youtube.com/watch?v={entry['id']}"
        title = entry.get('title', 'Неизвестный трек')
        parts = title.split(' - ', 1)
        artist_disp = parts[0] if len(parts) == 2 else "Unknown"
        title_disp = parts[1] if len(parts) == 2 else title
        audio_url, duration, thumb = await get_audio_url(video_url)
        if audio_url:
            await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "YouTube", ctx.author, thumb)
            return
        else:
            await ctx.send(f"⚠️ Не удалось извлечь звук из `{title}`, пробую следующий...")
    await ctx.send("❌ Все найденные варианты недоступны для извлечения аудио.")

@bot.command(name='playlistyt')
async def playlistyt(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ Вы не в голосовом канале!")
        return
    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)
    await ctx.send(f"🔍 Ищу плейлист YouTube по запросу: {query}")
    entries = await search_youtube_playlist(query, max_results=1)
    if not entries:
        await ctx.send("❌ Не удалось найти плейлист. Попробуйте другой запрос или используйте прямую ссылку.")
        return
    total = len(entries)
    await ctx.send(f"📋 Найдено треков в плейлисте: {total}. Добавляю в очередь...")
    added = 0
    for entry in entries:
        video_url = f"https://youtube.com/watch?v={entry['id']}"
        title = entry.get('title', 'Неизвестный трек')
        audio_url, duration, thumb = await get_audio_url(video_url)
        if audio_url:
            parts = title.split(' - ', 1)
            artist_disp = parts[0] if len(parts) == 2 else "Unknown"
            title_disp = parts[1] if len(parts) == 2 else title
            await play_song(ctx, vc, audio_url, title_disp, artist_disp, duration, "YouTube (плейлист)", ctx.author, thumb)
            added += 1
            await asyncio.sleep(0.3)
        else:
            await ctx.send(f"⚠️ Не удалось извлечь аудио для `{title}`, пропускаю.")
    await ctx.send(f"✅ Добавлено **{added}** из {total} треков.")

# --- VK команда (playvk) ---
@bot.command(name='playvk')
async def playvk(ctx, url: str):
    if not ctx.author.voice:
        await ctx.send("❌ Вы не в голосовом канале!")
        return
    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)

    entries = await extract_vk_playlist(url)
    if not entries:
        await ctx.send("❌ Не удалось извлечь треки из VK.")
        return
    added = 0
    for entry in entries:
        audio_url = entry.get('url')
        if not audio_url:
            continue
        title = entry.get('title', 'Неизвестный трек')
        duration = entry.get('duration', 0)
        thumbnail = entry.get('thumbnail')
        parts = title.split(' - ', 1)
        artist = parts[0] if len(parts) == 2 else "Unknown Artist"
        title_song = parts[1] if len(parts) == 2 else title
        await play_song(ctx, vc, audio_url, title_song, artist, duration, "VK", ctx.author, thumbnail)
        added += 1
        await asyncio.sleep(0.3)
    await ctx.send(f"✅ Добавлено **{added}** треков из VK.")

# --- SoundCloud ---
@bot.command(name='playsc')
async def playsc(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ Вы не в голосовом канале!")
        return
    vc = ctx.voice_client
    if vc is None:
        vc = await ctx.author.voice.channel.connect()
    elif vc.channel != ctx.author.voice.channel:
        await vc.move_to(ctx.author.voice.channel)
    await play(ctx, query=f"scsearch:{query}")

# --- Управление ---
@bot.command(name='skip')
async def skip(ctx):
    vc = ctx.voice_client
    if vc and vc.is_playing():
        vc.stop()
        await force_stop_voice(vc)
        await ctx.send("⏭️ Пропущено")
    else:
        await ctx.send("❌ Не играет")

@bot.command(name='stop')
async def stop(ctx):
    if ctx.voice_client:
        ctx.voice_client.stop()
        cleanup_guild(ctx.guild.id)
        await ctx.voice_client.disconnect()
        await ctx.send("⏹️ Остановлено")
    else:
        await ctx.send("❌ Бот не в канале")

@bot.command(name='queue')
async def show_queue(ctx):
    q = get_queue(ctx.guild.id)
    if q.empty():
        await ctx.send("📭 Очередь пуста")
        return
    songs = []
    temp = []
    while not q.empty():
        item = await q.get()
        songs.append(item['title'])
        temp.append(item)
    for item in temp:
        await q.put(item)
    queue_message = "📜 **Очередь:**\n" + "\n".join(f"{i+1}. {t}" for i, t in enumerate(songs[:10]))
    if len(songs) > 10:
        queue_message += f"\n*и ещё {len(songs) - 10} треков...*"
    await ctx.send(queue_message)

@bot.command(name='pause')
async def pause(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.pause()
        await ctx.send("⏸️ Пауза")
    else:
        await ctx.send("❌ Не играет")

@bot.command(name='resume')
async def resume(ctx):
    if ctx.voice_client and ctx.voice_client.is_paused():
        ctx.voice_client.resume()
        await ctx.send("▶️ Продолжаем")
    else:
        await ctx.send("❌ Не на паузе")

@bot.command(name='volume')
async def volume(ctx, vol: int):
    if vol < 0:
        vol = 0
    if vol > 100:
        vol = 100
    volume_levels[ctx.guild.id] = vol
    vc = ctx.voice_client
    if vc and vc.source and hasattr(vc.source, 'volume'):
        vc.source.volume = vol / 100
    await ctx.send(f"🔊 Громкость установлена на {vol}%")
    if ctx.guild.id in player_controllers:
        await player_controllers[ctx.guild.id].update_embed()

@bot.command(name='seek')
async def seek(ctx, seconds: int):
    vc = ctx.voice_client
    if not vc or not vc.is_playing():
        await ctx.send("❌ Сейчас ничего не играет.")
        return
    hist = HISTORY.get(ctx.guild.id, [])
    if not hist:
        await ctx.send("❌ Не удалось определить текущий трек.")
        return
    current_track = hist[-1]
    url = current_track['url']
    try:
        seek_options = ffmpeg_options.copy()
        seek_options['before_options'] = f'-ss {seconds} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5'
        audio = discord.FFmpegPCMAudio(url, **seek_options)
        vol = volume_levels.get(ctx.guild.id, 50) / 100
        audio = discord.PCMVolumeTransformer(audio, volume=vol)
        vc.stop()
        vc.play(audio, after=lambda e: asyncio.run_coroutine_threadsafe(play_next(ctx, ctx.guild.id), bot.loop))
        await ctx.send(f"⏩ Перемотка на {seconds} сек.")
        logger.info(f"Seek {seconds} сек в гильдии {ctx.guild.id}")
    except Exception as e:
        await ctx.send(f"⚠️ Ошибка перемотки: {e}")

@bot.command(name='radio')
async def radio_mode(ctx):
    current = RADIO_MODE.get(ctx.guild.id, False)
    RADIO_MODE[ctx.guild.id] = not current
    status = "включён" if not current else "выключен"
    await ctx.send(f"📻 Радиорежим {status}")

@bot.command(name='history')
async def show_history(ctx):
    hist = HISTORY.get(ctx.guild.id, [])
    if not hist:
        await ctx.send("📭 История пуста.")
        return
    out = "\n".join(f"{i+1}. {t['title']} — {t['artist']} (добавил: {t['requester'].display_name})" 
                    for i, t in enumerate(reversed(hist[-10:]), 1))
    await ctx.send(f"**Последние треки:**\n{out}")

@bot.command(name='save_playlist')
async def save_playlist(ctx, name: str):
    queue = get_queue(ctx.guild.id)
    items = []
    temp = []
    while not queue.empty():
        item = await queue.get()
        items.append(item)
        temp.append(item)
    for item in temp:
        await queue.put(item)
    if not items:
        await ctx.send("❌ Очередь пуста, нечего сохранять.")
        return
    tracks_json = json.dumps(items, ensure_ascii=False)
    async with aiosqlite.connect("playlists.db") as db:
        await db.execute(
            "INSERT OR REPLACE INTO user_playlists (user_id, name, tracks) VALUES (?, ?, ?)",
            (ctx.author.id, name, tracks_json)
        )
        await db.commit()
    await ctx.send(f"✅ Плейлист `{name}` сохранён ({len(items)} треков).")

@bot.command(name='load_playlist')
async def load_playlist(ctx, name: str):
    async with aiosqlite.connect("playlists.db") as db:
        async with db.execute(
            "SELECT tracks FROM user_playlists WHERE user_id = ? AND name = ?",
            (ctx.author.id, name)
        ) as cursor:
            row = await cursor.fetchone()
    if not row:
        await ctx.send(f"❌ Плейлист `{name}` не найден.")
        return
    items = json.loads(row[0])
    queue = get_queue(ctx.guild.id)
    for item in items:
        await queue.put(item)
    await ctx.send(f"📥 Плейлист `{name}` добавлен в очередь ({len(items)} треков).")

@bot.command(name='my_playlists')
async def my_playlists(ctx):
    async with aiosqlite.connect("playlists.db") as db:
        async with db.execute(
            "SELECT name FROM user_playlists WHERE user_id = ?", (ctx.author.id,)
        ) as cursor:
            rows = await cursor.fetchall()
    if not rows:
        await ctx.send("📭 У вас нет сохранённых плейлистов.")
        return
    names = [row[0] for row in rows]
    await ctx.send(f"📋 Ваши плейлисты: `{', '.join(names)}`")

@bot.command(name='info')
async def info(ctx):
    embed = discord.Embed(title="🎵 Музыкальный плеер", description="Доступные команды", color=discord.Color.blue())
    embed.add_field(name="!play", value="Воспроизвести трек/плейлист (YouTube, Spotify)", inline=False)
    embed.add_field(name="!playyt", value="Только YouTube", inline=False)
    embed.add_field(name="!playlistyt", value="Поиск плейлиста YouTube по названию", inline=False)
    embed.add_field(name="!playvk / !playsc", value="VK Music / SoundCloud", inline=False)
    embed.add_field(name="Управление", value="!skip, !stop, !queue, !pause, !resume, !volume, !seek", inline=False)
    embed.add_field(name="Дополнительно", value="!radio, !history, !save_playlist, !load_playlist, !my_playlists", inline=False)
    embed.add_field(name="Слэш-команды", value="/play, /skip, /stop, /queue, /pause, /resume, /volume", inline=False)
    await ctx.send(embed=embed)

# ---------- Слэш-команды ----------
@tree.command(name="play", description="Воспроизвести трек или плейлист (YouTube, Spotify)")
async def slash_play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await play.callback(ctx, query=query)

@tree.command(name="playyt", description="Поиск и воспроизведение только на YouTube")
async def slash_playyt(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await playyt.callback(ctx, query=query)

@tree.command(name="playlistyt", description="Поиск плейлиста на YouTube по названию")
async def slash_playlistyt(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await playlistyt.callback(ctx, query=query)

@tree.command(name="playvk", description="Воспроизвести трек или плейлист из VK")
async def slash_playvk(interaction: discord.Interaction, url: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await playvk.callback(ctx, url=url)

@tree.command(name="playsc", description="Поиск и воспроизведение на SoundCloud")
async def slash_playsc(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await playsc.callback(ctx, query=query)

@tree.command(name="skip", description="Пропустить текущий трек")
async def slash_skip(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await skip.callback(ctx)

@tree.command(name="stop", description="Остановить воспроизведение и очистить очередь")
async def slash_stop(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await stop.callback(ctx)

@tree.command(name="queue", description="Показать текущую очередь")
async def slash_queue(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await show_queue.callback(ctx)

@tree.command(name="pause", description="Поставить воспроизведение на паузу")
async def slash_pause(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await pause.callback(ctx)

@tree.command(name="resume", description="Возобновить воспроизведение")
async def slash_resume(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await resume.callback(ctx)

@tree.command(name="volume", description="Установить громкость (0-100)")
async def slash_volume(interaction: discord.Interaction, volume: int):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await volume.callback(ctx, vol=volume)

@tree.command(name="seek", description="Перемотать текущий трек на указанное количество секунд")
async def slash_seek(interaction: discord.Interaction, seconds: int):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await seek.callback(ctx, seconds=seconds)

@tree.command(name="radio", description="Включить/выключить радиорежим (автоподбор похожих треков)")
async def slash_radio(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await radio_mode.callback(ctx)

@tree.command(name="history", description="Показать последние 10 прослушанных треков")
async def slash_history(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await show_history.callback(ctx)

@tree.command(name="save_playlist", description="Сохранить текущую очередь как личный плейлист")
async def slash_save_playlist(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await save_playlist.callback(ctx, name=name)

@tree.command(name="load_playlist", description="Загрузить сохранённый плейлист в очередь")
async def slash_load_playlist(interaction: discord.Interaction, name: str):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await load_playlist.callback(ctx, name=name)

@tree.command(name="my_playlists", description="Список ваших сохранённых плейлистов")
async def slash_my_playlists(interaction: discord.Interaction):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    await my_playlists.callback(ctx)

@tree.command(name="info", description="Показать список всех команд бота")
async def slash_info(interaction: discord.Interaction):
    await interaction.response.defer()
    embed = discord.Embed(title="🎵 Музыкальный плеер", description="Доступные команды", color=discord.Color.blue())
    embed.add_field(name="/play", value="Воспроизвести трек/плейлист (YouTube, Spotify)", inline=False)
    embed.add_field(name="/playyt", value="Только YouTube", inline=False)
    embed.add_field(name="/playlistyt", value="Поиск плейлиста YouTube по названию", inline=False)
    embed.add_field(name="/playvk /playsc", value="VK Music / SoundCloud", inline=False)
    embed.add_field(name="Управление", value="/skip, /stop, /queue, /pause, /resume, /volume, /seek", inline=False)
    embed.add_field(name="Дополнительно", value="/radio, /history, /save_playlist, /load_playlist, /my_playlists", inline=False)
    embed.add_field(name="Префиксные команды", value="Все команды также доступны с ! (например, !play)", inline=False)
    await interaction.followup.send(embed=embed)

# ---------- Контекстное меню ----------
@tree.context_menu(name="Добавить в очередь")
async def add_to_queue_context(interaction: discord.Interaction, message: discord.Message):
    await interaction.response.defer()
    ctx = await commands.Context.from_interaction(interaction)
    if message.author == bot.user:
        await interaction.followup.send("❌ Нельзя добавлять сообщения бота.", ephemeral=True)
        return
    await play.callback(ctx, query=message.content)
    await interaction.followup.send("✅ Добавлено в очередь.", ephemeral=True)

# ================= БАЗА ДАННЫХ И СОБЫТИЯ =================
async def init_db():
    async with aiosqlite.connect("playlists.db") as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS user_playlists (
                user_id INTEGER,
                name TEXT,
                tracks TEXT,
                PRIMARY KEY (user_id, name)
            )
        ''')
        await db.commit()

@bot.event
async def on_voice_state_update(member, before, after):
    if member.id == bot.user.id:
        if before.channel and not after.channel:
            guild_id = before.channel.guild.id
            cleanup_guild(guild_id)
            logger.info(f"Бот отключён от {guild_id}, очередь очищена")
        elif not before.channel and after.channel:
            logger.info(f"Бот подключился к {after.channel.guild.id}")

@bot.event
async def on_ready():
    await bot.wait_until_ready()
    print(f'✅ Бот {bot.user} готов к работе!')
    logger.info(f'Бот {bot.user} запущен')
    await restore_queues()
    await init_db()
    try:
        await tree.sync()
        print("Слэш-команды синхронизированы")
    except Exception as e:
        print(f"Ошибка синхронизации слэш-команд: {e}")
    logger.info("Бот полностью инициализирован")

if __name__ == '__main__':
    if PROXY_URL:
        try:
            connector = ProxyConnector.from_url(PROXY_URL)
            bot.http.connector = connector
            print(f"[Прокси] Используется {PROXY_URL}")
            logger.info(f"Прокси установлен: {PROXY_URL}")
        except Exception as e:
            print(f"[Прокси] Ошибка настройки прокси: {e}")
    bot.run(DISCORD_TOKEN)#   d u m m y   c h a n g e  
 