import discord
from discord.ext import commands
import asyncio
import yt_dlp
import functools
from dotenv import load_dotenv
load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

# -----------------------
# ⚙️ Discord Bot Setup
# -----------------------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# -----------------------
# 🎵 yt_dlp + FFmpeg Setup
# -----------------------
YDL_OPTIONS = {
    'format': 'bestaudio[ext=m4a]/bestaudio/best',
    'quiet': True,
    'default_search': 'ytsearch',
    'noplaylist': True,
}


# ⚠️ Change this to your actual ffmpeg.exe path
FFMPEG_PATH = r"C:\Users\User\Downloads\ffmpeg-8.0-essentials_build\ffmpeg-8.0-essentials_build\bin\ffmpeg.exe"

# -----------------------
# 🔁 State Management
# -----------------------
repeat_flags = {}   # {guild_id: True/False}
current_songs = {}  # {guild_id: url}


# -----------------------
# 📡 Helper: Get Song Info
# -----------------------
async def get_info(url):
    loop = asyncio.get_event_loop()
    ydl = yt_dlp.YoutubeDL(YDL_OPTIONS)
    func = functools.partial(ydl.extract_info, url, download=False)
    try:
        info = await loop.run_in_executor(None, func)
        if 'entries' in info:
            info = info['entries'][0]
        return info
    except Exception as e:
        print(f"[ERROR] yt_dlp 오류: {e}")
        return None


# -----------------------
# 🔄 After Play (for repeat)
# -----------------------
async def after_play(ctx, error):
    if error:
        print(f"[ERROR] 재생 오류: {error}")
        return

    guild_id = ctx.guild.id
    if repeat_flags.get(guild_id, False) and ctx.voice_client:
        url = current_songs.get(guild_id)
        if url:
            info = await get_info(url)
            if info:
                stream_url = info['url']
                source = discord.FFmpegPCMAudio(
                    stream_url,
                    executable=FFMPEG_PATH,
                    before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -nostdin',
                    options='-vn -loglevel panic -f s16le -ar 48000 -ac 2'
                )   

                print(f"[DEBUG] Attempting to play stream: {stream_url}")
                ctx.voice_client.play(source, after=lambda e: bot.loop.create_task(after_play(ctx, e)))

                # diagnostic sleep check
                await asyncio.sleep(1)
                print(f"[DEBUG] Voice playing status: {ctx.voice_client.is_playing()}")
                print(f"[DEBUG] 반복 재생 중: {info.get('title', 'Unknown Title')}")


# -----------------------
# ▶️ Play Command
# -----------------------
@bot.command(name='play', aliases=['p', '재생'])
async def play(ctx, *, url):
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("먼저 음성 채널에 들어가야 합니다.")
        return

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        await channel.connect()
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    await ctx.send("노래 정보를 가져오는 중...")
    info = await get_info(url)
    print(f"[DEBUG] info keys: {list(info.keys())}")
    print(f"[DEBUG] URL extracted: {info.get('url')}")

    if not info:
        await ctx.send("노래 정보를 가져올 수 없습니다.")
        return

    guild_id = ctx.guild.id
    current_songs[guild_id] = url
    repeat_flags.setdefault(guild_id, False)

    stream_url = info['url']
    title = info.get('title', 'Unknown Title')

    if ctx.voice_client.is_playing():
        ctx.voice_client.stop()

    source = discord.FFmpegPCMAudio(
        stream_url,
        executable=FFMPEG_PATH,
        before_options='-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
        options='-vn -f s16le -ar 48000 -ac 2'
    )

    ctx.voice_client.play(source, after=lambda e: bot.loop.create_task(after_play(ctx, e)))
    await ctx.send(f"**{title}** 재생 시작")
    print(f"[DEBUG] Now playing: {title}")
    print(f"[DEBUG] Stream URL: {stream_url}")


# -----------------------
# ⏹️ Stop Command
# -----------------------
@bot.command(name='stop', aliases=['s', '종료'])
async def stop(ctx):
    guild_id = ctx.guild.id
    repeat_flags[guild_id] = False
    current_songs.pop(guild_id, None)

    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("재생 중지")
    else:
        await ctx.send("재생 중인 노래 없음")


# -----------------------
# 🚪 Leave Command
# -----------------------
@bot.command(name='leave', aliases=['l', '나가'])
async def leave(ctx):
    guild_id = ctx.guild.id
    repeat_flags[guild_id] = False
    current_songs.pop(guild_id, None)

    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("음성 채널 연결 해제")
    else:
        await ctx.send("음성 채널에 연결되어 있지 않음")


# -----------------------
# 🔁 Repeat Command
# -----------------------
@bot.command(name='repeat', aliases=['r', '반복'])
async def repeat(ctx, mode: str):
    guild_id = ctx.guild.id
    if mode.lower() in ["on", "켜기"]:
        repeat_flags[guild_id] = True
        await ctx.send("반복 모드 **켜짐**.")
    elif mode.lower() in ["off", "끄기"]:
        repeat_flags[guild_id] = False
        await ctx.send("반복 모드 **꺼짐**.")
    else:
        await ctx.send("사용법: `!repeat on` 또는 `!repeat off`")


# -----------------------
# 🚀 Run Bot
# -----------------------
bot.run(TOKEN)
