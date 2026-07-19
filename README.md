# ADK Skills

![ADK Skills title](./img/title.png)

ตัวอย่าง Google Agent Development Kit (ADK) ที่ใช้ Skills — มี 2 agents ใน repo นี้

## ตัวอย่าง Prompt (ภาษาไทย)

รัน `adk web` แล้วเลือก agent จากนั้นลองส่งข้อความเหล่านี้

### Blog Skills Agent

1. `"ฉันมีบล็อกโพสต์ชื่อ 'เริ่มต้นกับ BigQuery AI' ช่วยรีวิว SEO ให้หน่อยได้ไหม?"` — inline skill
2. `"ช่วยเขียนบทนำสั้นๆเกี่ยวกับ BugQuery AI และทำให้ SEO-friendly ด้วย"` — blog-writer + seo-checklist
3. `"ใช้ content research skill ช่วยวิจัยเรื่อง BigQuery AI ให้หน่อย"` — external / file skill + L3 resource
4. `"ฉันต้องการ skill ใหม่สำหรับรีวิวโค้ด Python เรื่องช่องโหว่ความปลอดภัย ช่วยสร้าง SKILL.md ให้ได้ไหม?"` — skill factory

### Marathon Planner Agent

1. `"ช่วยวางแผน Run club สำหรับ 100 คน เส้นทางวิ่ง 10 กม. จากสวนลุมพินีไปสะพานเขียว"` — park connector route
2. `"ออกแบบเส้นทางวิ่งรูปหัวใจภายในสวนลุมพินี ระยะ 10 กม."` — heart shape ในสวน
3. `"วางแผนงานมาราธอนสำหรับผู้เข้าร่วม 5,000 คน ที่กรุงเทพ ช่วงเช้า"` — race event + logistics

## Slides

- [GoogleNextBKK — Build ADK Agent with Skill](https://docs.google.com/presentation/d/e/2PACX-1vST1j01s6fEWhbd5KD6RrGNRP6QAe7F865ksygJuXjjkUGJtxOO2wEGG1i5_5fczsrd54MVf7vnnXpL/pub)

## Agents

| Agent | โฟลเดอร์ | แหล่งที่มา |
|-------|----------|------------|
| Blog Skills Agent | [`blog_skills_agent/`](./blog_skills_agent/) | [Developer’s Guide to Building ADK Agents with Skills](https://developers.googleblog.com/developers-guide-to-building-adk-agents-with-skills/) · sample จาก [google/adk-samples](https://github.com/google/adk-samples/tree/main/python/agents/agent-skills-tutorial) |
| Marathon Planner Agent | [`marathon_planner_agent/`](./marathon_planner_agent/) | [GoogleCloudPlatform/next-26-keynotes](https://github.com/GoogleCloudPlatform/next-26-keynotes) · path `devkey/demo-1` |

### Source repositories

- **Marathon Planner:** https://github.com/GoogleCloudPlatform/next-26-keynotes  
  (`devkey/demo-1` — Building ADK Agents with Skills and Tools)
- **Blog Skills patterns:** https://github.com/google/adk-samples  
  (`python/agents/agent-skills-tutorial`)
- **ADK Skills docs:** https://adk.dev/skills/

## Prerequisites

- Python 3.12+
- pip
- API keys ตาม agent ที่จะรัน (`GOOGLE_API_KEY` และ/หรือ Google Cloud + Maps)

## Quick Start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

สำหรับ Marathon Planner ให้ติดตั้ง dependencies เพิ่ม:

```bash
pip install -r marathon_planner_agent/requirements.txt
```

ตั้งค่า environment:

```bash
cp .env.example .env
# แก้ไข .env ตามที่ต้องการ

# Marathon Planner
cp marathon_planner_agent/sample.env marathon_planner_agent/.env
# ใส่ GOOGLE_CLOUD_PROJECT และ GOOGLE_MAPS_API_KEY
```

รัน Web UI:

```bash
adk web
```

for github codespace 
```bash
adk web . \
  --host 0.0.0.0 \
  --port 8000 \
  --allow_origins "*"
```

เปิด `http://127.0.0.1:8000` แล้วเลือก agent ที่ต้องการ

## Project Structure

```
adk-skills/
├── blog_skills_agent/          # 4 skill patterns (inline / file / external / meta)
├── marathon_planner_agent/     # Marathon planner + skills + MCP mapping tools
├── requirements.txt
├── .env.example
└── README.md
```

รายละเอียดเพิ่มเติมดูใน README ของแต่ละ agent:

- [blog_skills_agent/README.md](./blog_skills_agent/README.md)
- [marathon_planner_agent/README.md](./marathon_planner_agent/README.md)
