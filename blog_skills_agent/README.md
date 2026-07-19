# ADK Agents with Skills

Blog-writing agent ที่ใช้ [ADK SkillToolset](https://adk.dev/skills/) ตามบทความ [Developer’s Guide to Building ADK Agents with Skills](https://developers.googleblog.com/developers-guide-to-building-adk-agents-with-skills/)

สาธิต 4 skill patterns และ progressive disclosure (L1 / L2 / L3)

## Patterns

| Pattern | Skill | วิธีโหลด |
|---------|-------|----------|
| 1. Inline | `seo-checklist` | `models.Skill` ในโค้ด |
| 2. File-based | `blog-writer` | `load_skill_from_dir` + `SKILL.md` |
| 3. External | `content-research-writer` | โหลดจาก directory (รูปแบบ community skill) |
| 4. Meta / factory | `skill-creator` | skill ที่สร้าง `SKILL.md` ใหม่ได้ |

`SkillToolset` สร้าง tools อัตโนมัติ: `list_skills` (L1), `load_skill` (L2), `load_skill_resource` (L3)

## Prerequisites

- Python 3.11+
- [Google API key](https://aistudio.google.com/apikey)

## Quick Start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

cp .env.example app/.env
# ใส่ GOOGLE_API_KEY ใน app/.env

adk web
```

เลือก agent `app` แล้วลองคุยกับ agent

ตัวอย่าง prompt ภาษาไทยอยู่ที่ [README หลัก](../README.md#ตัวอย่าง-prompt-ภาษาไทย)

## โครงสร้างโปรเจกต์

```
adk-skills/
├── app/
│   ├── agent.py              # Root agent + SkillToolset
│   └── skills/
│       ├── blog-writer/
│       │   ├── SKILL.md
│       │   └── references/style-guide.md
│       └── content-research-writer/
│           ├── SKILL.md
│           └── references/seo-guidelines.md
├── .env.example
├── pyproject.toml
└── README.md
```
