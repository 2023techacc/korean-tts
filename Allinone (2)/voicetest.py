from jamo import h2j, j2hcj
import os
import time
import discord
from discord.ext import commands
import asyncio
import nacl
from discord import FFmpegPCMAudio

ffmpeg_path=r"DIR_HERE"
token="TOKEN_HERE"



ffmpeg_path+=r"\ffmpeg.exe"

def tofiles(text):
    compvowel={'ㅘ':'ㅗㅏ','ㅙ':'ㅗㅐ','ㅚ':'ㅗㅐ','ㅝ':'ㅜㅓ','ㅞ':'ㅜㅔ','ㅟ':'ㅜㅣ','ㅢ':'ㅡㅣ'}
    sound={'ㅔ':'ㅐ','ㅖ':'ㅒ'}
    eng={'ㅏ':'a','ㅓ':'eo','ㅐ':'ae','ㅡ':'eu','ㅣ':'i','ㅗ':'o','ㅜ':'u','ㅑ':'ya','ㅒ':'yae','ㅕ':'yeo','ㅛ':'yo','ㅠ':'yu','ㄱ':'g','ㄴ':'n','ㄷ':'d','ㄹ':'l','ㅁ':'m','ㅂ':'b','ㅅ':'s','ㅇ':'ng','ㅈ':'j','ㅊ':'ch','ㅋ':'k','ㅌ':'t','ㅍ':'p','ㅎ':'h','ㄲ':'gg','ㄸ':'dd','ㅆ':'ss','ㅉ':'jj','ㅃ':'bb'}
    end={'ㅅ':'ㄷ','ㅈ':'ㄷ','ㅊ':'ㄷ','ㅋ':'ㄱ','ㅌ':'ㄷ','ㅍ':'ㅂ','ㅎ':'ㄷ','ㄲ':'ㄱ','ㄸ':'ㄷ','ㅉ':'ㄷ','ㅃ':'ㅂ','ㅆ':'ㄷ','ㄳ':'ㄱ','ㄵ':'ㄴ','ㄶ':'ㄴ','ㅄ':'ㅂ','ㄼ':'ㄹ','ㄽ':'ㄹ','ㄾ':'ㄹ','ㅀ':'ㄹ','ㄺ':'ㄱ','ㄻ':'ㅁ','ㄿ':'ㅂ'}
    consts={'ㄱ','ㄴ','ㄷ','ㄹ','ㅁ','ㅂ','ㅅ','ㅇ','ㅈ','ㅊ','ㅋ','ㅌ','ㅍ','ㅎ','ㄲ','ㄸ','ㅃ','ㅆ','ㅉ','ㄳ','ㄵ','ㄶ','ㅄ','ㄼ','ㄽ','ㄾ','ㅀ','ㄺ','ㄻ','ㄿ'}
    solcon={'ㄴ','ㄹ','ㅁ','ㅇ'}
    textlist=list(text)
    d=[]
    for i in textlist:
        a=list(j2hcj(h2j(i)))
        b=[]
        c=[]
        if a[-1] in consts:
            a[-1]=end.get(a[-1],a[-1])
        for j in a:
            b+=list(compvowel.get(j,j))
        for j in b:
            c+=list(sound.get(j,j))
            
        if c==[' ']:
            d.append('stop')
        elif c[0]=='ㅇ' and c[-1] not in solcon and len(c)==2+int(c[-1] in consts):
            d.append('')
            for j in range(len(c)-1):
                d[-1]+=(eng[c[j+1]])
        elif c[0]=='ㅇ' and c[-1] in solcon:
            for _ in range(len(c)-1):
                d.append('')
            for j in range(len(c)-1):
                d[-len(c)+1+j]+=eng[c[j+1]]
        elif c[0]=='ㅇ':
            d.append('')
            d.append('')
            d[-2]+=(eng[c[1]])
            for j in range(len(c)-2):
                d[-1]+=eng[c[j+2]]
        elif len(c)==2 and c[0] in consts:
            d.append('')
            for j in range(2):
                d[-1]+=eng[c[j]]
        elif len(c)==2:
            d.append('')
            d.append('')
            d[-2]+=eng[c[0]]
            d[-1]+=eng[c[1]]
        elif c[-1] in consts and c[-1] not in solcon:
            d.append('')
            d.append('')
            for j in range(2):
                d[-2]+=eng[c[j]]
            for j in range(len(c)-2,len(c)):
                d[-1]+=eng[c[j]]
        elif c[-1] in solcon and len(c)==4:
            for _ in range(3):
                d.append('')
            for j in range(2):
                d[-3]+=eng[c[j]]
            d[-2]+=eng[c[2]]
            d[-1]+=eng[c[3]]
        elif c[-1] in solcon and len(c)==3:
            for _ in range(2):
                d.append('')
            for j in range(2):
                d[-2]+=eng[c[j]]
            d[-1]+=eng[c[2]]
        else:
            d.append('')
            d.append('')
            for j in range(2):
                d[-2]+=eng[c[j]]
            d[-1]+=eng[c[-1]]

    return d
intents=discord.Intents.default()
bot=commands.Bot(command_prefix='!',intents=intents)
intents.message_content=True

FFMPEG_PATH=r"C:\voicething\ffmpeg\ffmpeg.exe"

@bot.event
async def on_ready():
    print(f"로그인: {bot.user}")

@bot.command()
async def join(ctx):
    print("trying")
    if ctx.author.voice:
        channel=ctx.author.voice.channel
        await channel.connect()
        await ctx.send("음성 채널에 들어갔어요")
    else:
        await ctx.send("먼저 음성 채널에 들어가주세요.")

@bot.command()
async def leave(ctx):
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("나갔어요!")
    else:
        await ctx.send("음성 채널에 있지 않아요.")

@bot.command()
async def say(ctx,*,text):
    
    base_path = os.path.dirname(__file__)
    sound_folder = os.path.join(base_path, "sound")

    d=tofiles(text)

    playlist=[os.path.join(sound_folder,i+".wav") for i in d]
    
    vc = ctx.voice_client
    if not vc:
        await ctx.send("먼저 !join 으로 음성 채널에 들어가주세요.")
        return

    for song in playlist:
        if song.endswith("stop.wav"):
            await asyncio.sleep(0.5)
            continue
        if not os.path.exists(song):
            print(f"파일 없음:{song}")
            continue
        print('start') 
        audio_source=FFmpegPCMAudio(song,executable=ffmpeg_path)
        vc.play(audio_source)
        while vc.is_playing():
            await asyncio.sleep(0.05)

    #await ctx.send(f"{filename} 재생 완료 ✅")

bot.run(token)
