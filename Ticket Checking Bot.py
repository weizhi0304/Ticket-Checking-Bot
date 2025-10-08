# ==================================================
# Section 1：設定與初始化
# ==================================================
import discord
from discord.ext import commands, tasks
import requests, datetime, json, os
from bs4 import BeautifulSoup

# ------------------ 自動建立 config.json ------------------
def load_config():
    path = "config.json"
    if not os.path.exists(path):
        sample = {
            "TOKEN": "請填入你的BOT TOKEN",
            "CHANNELID": 123456789012345678
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sample, f, ensure_ascii=False, indent=4)
        print("⚠️ 已自動建立 config.json，請填入正確 TOKEN 與 CHANNELID 後重新執行。")
        exit()

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        print("❌ config.json 格式錯誤，請確認內容符合 JSON 格式。")
        exit()

config = load_config()
TOKEN = config.get("TOKEN")
CHANNELID = int(config.get("CHANNELID", 0))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

USERCONFIGS = {}
SESSION = {}

# ==================================================
# Section 2：API 函式（def）
# ==================================================
def _ubus_session():
    homeurl = "https://booking.ubus.com.tw/TKT/booking"
    s = requests.Session()
    r = s.get(homeurl, timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    tok_tag = soup.find("input", {"name": "__RequestVerificationToken"})
    token = tok_tag["value"] if tok_tag else ""
    s.cookies.set("__RequestVerificationToken", token)
    headers = {
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "Content-Type": "application/json",
        "Origin": "https://booking.ubus.com.tw",
        "Referer": homeurl,
        "User-Agent": "Mozilla/5.0",
        "X-Requested-With": "XMLHttpRequest",
        "__RequestVerificationToken": token,
    }
    return s, headers


def fetch_area_and_station():
    url = "https://booking.ubus.com.tw/TKT/Get_origin"
    s, headers = _ubus_session()
    resp = s.post(url, json={}, headers=headers, timeout=15)
    resp.raise_for_status()
    j = resp.json()
    areas = [(i["RegionName"], i["RegionID"]) for i in j.get("Data1", [])]
    stations = {}
    for it in j.get("Data2", []):
        rid = it["RegionID"]
        stations.setdefault(rid, []).append({
            "StnName": it["StnName"],
            "StationID": it["StationID"],
        })
    return areas, stations


def fetch_destination_area_and_station(regionid, originid):
    url = "https://booking.ubus.com.tw/TKT/Get_destination"
    s, headers = _ubus_session()
    resp = s.post(url, json={"RegionID_F": regionid, "Origin_Stn": originid}, headers=headers, timeout=15)
    j = resp.json()
    areas = [(i["RegionName"], i["RegionID"]) for i in j.get("Data1", [])]
    stations = {}
    for i in j.get("Data2", []):
        stations.setdefault(i["RegionID"], []).append({"StnName": i["StnName"], "StationID": i["StationID"]})
    return areas, stations


def queryubus(config):
    url = "https://booking.ubus.com.tw/TKT/Get_SchInfo"
    s, headers = _ubus_session()
    payload = {
        "RegionID_F": config["regionid"],
        "Origin_Stn": config["originid"],
        "RegionID_T": config["regionid"],
        "Destination_Stn": config["destid"],
        "StartTime": f"{config['date']}T00:00:00.000Z",
        "ticket_cnt1": 1,
    }
    resp = s.post(url, json=payload, headers=headers, timeout=15)
    return resp.json() if resp.ok else {"Data": []}

# ==================================================
# Section 3：Discord UI 類別（class）
# ==================================================
class AreaSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.areas, self.stations = fetch_area_and_station()
        for name, id_ in self.areas:
            btn = discord.ui.Button(label=name, style=discord.ButtonStyle.primary)
            btn.callback = self.cb_factory(id_, name)
            self.add_item(btn)

    def cb_factory(self, areaid, areaname):
        async def _cb(interaction):
            SESSION[interaction.user.id] = {"regionid": areaid}
            await interaction.response.send_message(f"已選出發地區：{areaname}", ephemeral=True)
            msg = await interaction.followup.send("請選擇出發站：", view=StationSelectView(areaid, self.stations[areaid]))
            await msg.delete(delay=600)
        return _cb


class StationSelectView(discord.ui.View):
    def __init__(self, areaid, stations):
        super().__init__(timeout=120)
        self.areaid = areaid
        for s in stations:
            btn = discord.ui.Button(label=s["StnName"], style=discord.ButtonStyle.secondary)
            btn.callback = self.cb_factory(s)
            self.add_item(btn)

    def cb_factory(self, stninfo):
        async def _cb(interaction):
            uid = interaction.user.id
            SESSION[uid]["originid"] = stninfo["StationID"]
            SESSION[uid]["originname"] = stninfo["StnName"]
            await interaction.response.send_message(f"已選出發站：{stninfo['StnName']}", ephemeral=True)
            areas, stations = fetch_destination_area_and_station(self.areaid, stninfo["StationID"])
            if not stations:
                msg = await interaction.followup.send("⚠️ 查不到目的站，請換出發站")
                await msg.delete(delay=600)
                return
            msg = await interaction.followup.send("請選擇目的地區：", view=DestAreaSelectView(self.areaid, stninfo["StationID"], areas, stations))
            await msg.delete(delay=600)
        return _cb


class DestAreaSelectView(discord.ui.View):
    def __init__(self, areaid, originid, areas, stations):
        super().__init__(timeout=120)
        self.areaid, self.originid, self.stations = areaid, originid, stations
        for name, id_ in areas:
            if id_ in stations:
                btn = discord.ui.Button(label=name, style=discord.ButtonStyle.primary)
                btn.callback = self.cb_factory(id_, name)
                self.add_item(btn)

    def cb_factory(self, regionid, areaname):
        async def _cb(interaction):
            await interaction.response.send_message(f"已選目的地區：{areaname}", ephemeral=True)
            msg = await interaction.followup.send("請選擇目的站：", view=DestStationSelectView(self.areaid, self.originid, self.stations[regionid]))
            await msg.delete(delay=600)
        return _cb


class DestStationSelectView(discord.ui.View):
    def __init__(self, areaid, originid, stations):
        super().__init__(timeout=120)
        self.areaid, self.originid = areaid, originid
        for s in stations:
            btn = discord.ui.Button(label=s["StnName"], style=discord.ButtonStyle.success)
            btn.callback = self.cb_factory(s)
            self.add_item(btn)

    def cb_factory(self, stninfo):
        async def _cb(interaction):
            uid = interaction.user.id
            SESSION[uid]["destid"] = stninfo["StationID"]
            SESSION[uid]["destname"] = stninfo["StnName"]
            await interaction.response.send_message(f"已選目的站：{stninfo['StnName']}", ephemeral=True)
            today = datetime.date.today()
            dates = [(today + datetime.timedelta(days=i)).strftime("%Y-%m-%d") for i in range(8)]
            options = [discord.SelectOption(label=d, value=d) for d in dates]
            msg = await interaction.followup.send("請選擇日期：", view=DateSelectView(self.areaid, self.originid, stninfo["StationID"], options, uid))
            await msg.delete(delay=600)
        return _cb


class DateSelectView(discord.ui.View):
    def __init__(self, areaid, originid, destid, options, uid):
        super().__init__(timeout=120)
        self.areaid, self.originid, self.destid, self.uid = areaid, originid, destid, uid
        self.add_item(DateSelect(options, self))


class DateSelect(discord.ui.Select):
    def __init__(self, options, parent):
        super().__init__(placeholder="請選日期", min_values=1, max_values=1, options=options)
        self.parent = parent

    async def callback(self, interaction):
        uid = interaction.user.id
        USERCONFIGS[uid] = {
            "regionid": self.parent.areaid,
            "originid": self.parent.originid,
            "originname": SESSION[uid].get("originname", ""),
            "destid": self.parent.destid,
            "destname": SESSION[uid].get("destname", ""),
            "date": self.values[0],
        }
        await interaction.response.send_message(f"✅ 已選日期：{self.values[0]}，查票設定完成！", ephemeral=True)
        if not check_ticket.is_running():
            check_ticket.start()

# ==================================================
# Section 4：Discord 指令與任務
# ==================================================
@tasks.loop(minutes=5)
async def check_ticket():
    channel = bot.get_channel(CHANNELID)
    now = datetime.datetime.now()
    for uid, config in USERCONFIGS.items():
        try:
            data = queryubus(config)
            rows = []
            urgent = False
            for i in data.get("Data", []):
                vacancy = int(i.get("Vacancy", 0))
                if vacancy > 0:
                    urgent |= vacancy <= 5
                    rows.append(i)
            if rows:
                color = discord.Color.red() if urgent else discord.Color.green()
                title = f"🔥 緊急！剩餘座位稀少 ({config['originname']} → {config['destname']})" if urgent else f"🚩 有票！{config['originname']} → {config['destname']}"
                embed = discord.Embed(title=title, color=color)
                tag_sent = False  # 新增旗標
                for idx, i in enumerate(rows, 1):
                    schno = i.get("SchNo", "")
                    schdate = i.get("SchDate", "")
                    schtime = i.get("SchTime", "")
                    price = i.get("LinePrice", "")
                    vacancy = int(i.get("Vacancy", 0))

                    seat_text = f"剩餘座位：`{vacancy}`"
                    if vacancy <= 5:
                        seat_text += " 🔥"

                    booking_url = f"https://booking.ubus.com.tw/TKT/booking"

                    # 每班次獨立一張 Embed 卡片
                    embed_color = discord.Color.red() if vacancy <= 5 else discord.Color.green()
                    embed = discord.Embed(
                        title=f"🚍 {config['originname']} → {config['destname']}",
                        description=f"📅 {schdate} {schtime} | 💰 票價：{price} 元",
                        color=embed_color
                    )
                    embed.add_field(name="🪪 車次代碼", value=schno, inline=False)
                    embed.add_field(name="🪑 狀態", value=seat_text, inline=False)
                    embed.add_field(
                        name="🔗 訂票連結",
                        value=f"[點我立即訂票]({booking_url})",
                        inline=False
                    )
                    embed.set_footer(text=f"查詢時間：{now.strftime('%Y-%m-%d %H:%M:%S')}")

                    # 只有第一張 Tag 使用者
                    if not tag_sent:
                        await channel.send(content=f"<@{uid}>", embed=embed)
                        tag_sent = True
                    else:
                        await channel.send(embed=embed)
        except Exception as e:
            await channel.send(f"查票錯誤 (User {uid})：{e}")

# === 指令區 ===
@bot.command(name="路線設定")
async def set_route(ctx):
    msg = await ctx.send("請先選擇【出發地區】：", view=AreaSelectView())
    await msg.delete(delay=600)


@bot.command(name="查票頻率")
async def set_interval(ctx, minutes: int):
    if minutes < 1:
        await ctx.send("❌ 頻率不能小於 1 分鐘")
        return
    check_ticket.change_interval(minutes=minutes)
    await ctx.send(f"✅ 已將查票頻率設為 {minutes} 分鐘")


@bot.command(name="我的路線")
async def myroute(ctx):
    uid = ctx.author.id
    if uid not in USERCONFIGS:
        await ctx.send("❌ 你還沒有設定路線，請先使用 `!路線設定`")
        return
    cfg = USERCONFIGS[uid]
    embed = discord.Embed(title="🚌 你的路線設定", color=discord.Color.blue())
    embed.add_field(name="出發站", value=cfg["originname"])
    embed.add_field(name="目的站", value=cfg["destname"])
    embed.add_field(name="日期", value=cfg["date"])
    embed.set_footer(text=f"使用者：{ctx.author.display_name}")
    await ctx.send(embed=embed)


@bot.command(name="清除路線")
async def clearroute(ctx):
    uid = ctx.author.id
    if uid in USERCONFIGS:
        USERCONFIGS.pop(uid)
        await ctx.send(f"🗑️ {ctx.author.mention} 你的路線設定已清除")
    else:
        await ctx.send("❌ 你目前沒有設定任何路線")


@bot.command(name="所有路線")
async def allroutes(ctx):
    if not USERCONFIGS:
        await ctx.send("📭 目前沒有任何人設定路線")
        return
    embed = discord.Embed(title="📋 所有使用者的路線設定", color=discord.Color.purple())
    for uid, cfg in USERCONFIGS.items():
        user = await bot.fetch_user(uid)
        embed.add_field(name=user.display_name, value=f"出發：{cfg['originname']}\n目的：{cfg['destname']}\n日期：{cfg['date']}", inline=False)
    await ctx.send(embed=embed)


@bot.command(name="停止查票")
async def stopquery(ctx):
    if check_ticket.is_running():
        check_ticket.cancel()
        await ctx.send("✅ 已停止查票")
    else:
        await ctx.send("🚫 查票未在執行中")


@bot.command(name="開始查票")
async def startquery(ctx):
    if not check_ticket.is_running():
        check_ticket.start()
        await ctx.send("✅ 已重新啟動查票")
    else:
        await ctx.send("🚩 查票已在執行")

@bot.command(name="help")
async def show_help(ctx):
    embed = discord.Embed(
        title="📘 指令清單",
        description="以下是目前可用的所有指令：",
        color=discord.Color.teal()
    )

    embed.add_field(name="!路線設定", value="開始設定查票路線（出發地區 → 目的地 → 日期）", inline=False)
    embed.add_field(name="!我的路線", value="查看你目前設定的出發站、目的站與日期", inline=False)
    embed.add_field(name="!清除路線", value="刪除你目前的路線設定", inline=False)
    embed.add_field(name="!所有路線", value="查看所有使用者設定的查票路線（群組總覽）", inline=False)
    embed.add_field(name="!查票頻率 <分鐘>", value="設定查票的間隔時間（預設為 5 分鐘）", inline=False)
    embed.add_field(name="!開始查票", value="手動啟動查票任務", inline=False)
    embed.add_field(name="!停止查票", value="停止查票任務", inline=False)
    embed.add_field(name="!help", value="顯示本說明清單", inline=False)

    embed.set_footer(text=f"使用者：{ctx.author.display_name}")

    await ctx.send(embed=embed)


# ==================================================
# Section 5：Bot 啟動
# ==================================================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    channel = bot.get_channel(CHANNELID)
    if channel:
        await channel.send("🚌 查票機器人已啟動，請輸入 !路線設定 開始設定路線")

bot.run(TOKEN)
