# AuraAgent

**[中文文档](README-cn.md)** · **[Looking for the technical architecture instead? →](docs/ARCHITECTURE.md)**

*A personal AI team that runs on your own computer — and never hides what it's doing.*

---

## What Is AuraAgent?

Imagine having a small, capable assistant team living on your own computer: one that can research topics on the web, manage your notes and calendar, organize your files, remember what you've told it before, and even learn brand-new skills when you ask it to. And unlike most AI tools, it never works in a black box — every step it takes is shown to you, out loud, as it happens.

That's AuraAgent. It isn't a chatbot that only answers questions in a text box. It's a system that can actually *do* things for you — read a file, check the news on a topic, schedule an event, write a document, connect to your real Google Calendar — while always telling you exactly what it's about to do before anything that matters actually happens.

It runs entirely on your own machine, using your own API key. Nothing about how it thinks or what it does is hidden inside code you can't see into.

This document is written for anyone curious about what AuraAgent is and how to use it — no programming background required. If you're a developer looking for class names, file paths, and design rationale, the [technical architecture document](docs/ARCHITECTURE.md) is where that lives.

---

## Chapter 1 — The Philosophy Behind It

Three ideas shape everything AuraAgent does, and they're worth understanding before anything else, because they explain *why* it behaves the way it does.

**1. Nothing is hidden.**
Every step AuraAgent takes — what it's thinking, which tool it's about to use, what came back from that tool — is printed in front of you in real time, and saved to a log you can review later. There's no "trust me, it's working on it" moment where the reasoning disappears into a black box. You can watch it think, the way you'd watch a colleague reason out loud.

**2. It asks before anything risky.**
AuraAgent sorts its own actions by how much damage they could do if something went wrong. Reading a file or looking something up online? It just does it — low risk, reversible. Deleting a file, taking a screenshot of your whole screen, installing a brand-new capability, or connecting to an outside service? It stops, lays out exactly what it's about to do and why, and waits for your explicit yes or no. Every time. No exceptions, no "just this once."

**3. It can grow — but only with your permission.**
If AuraAgent runs into something it doesn't know how to do, it doesn't simply fail. It can write itself a small new tool, recruit a new specialist onto its team, or connect to an outside service that already knows how to help — but never without first walking you through exactly what it's proposing, in full, so your "yes" is an informed one rather than a rubber stamp.

---

## Chapter 2 — What Can It Actually Do?

Here's a tour of AuraAgent's capabilities, grouped by what you'd actually use them for:

- **Research the web.** It can fetch and read web pages, submit forms, and pull down files. It also has a few built-in specialties for market/industry research — give it a topic like "electric vehicles" or "AI chips" and it'll pull recent real headlines on new products, technology trends, or major company moves.
- **Take notes.** It can create, read, update, and full-text search a library of Markdown notes — which you can point at a folder you already use, like an Obsidian vault, instead of a folder AuraAgent manages for you.
- **Manage your calendar and to-do list.** By default this is a local, private calendar/task list. If you want, you can connect your *real* Google Calendar instead (a one-time setup — see Chapter 5), and it'll read, create, update, and delete actual events on it.
- **Manage files on your computer** — inside a folder you designate, never your whole hard drive. It can list, read, write, move, copy, and search files there, including a precise partial edit (replace one exact piece of text in place) instead of rewriting a whole file for a small change — handy for a large note or document it's maintaining incrementally. It can also reach Anthropic's official file-management tools for more advanced operations.
- **Read and produce real documents.** Word, PowerPoint, and Excel files can be read, created from scratch, and edited (appending content, or a simple find/replace); PDFs can be read (text-based ones — there's no OCR for scanned pages). No separate setup needed.
- **Control your desktop**, in small, supervised ways: read or write your clipboard, take a screenshot, see what programs are running and close one that's stuck, or pop up a desktop notification.
- **Remember things about you.** It can hold both a quick, structured profile of your standing preferences and habits (shown to it automatically every time), and a searchable library of specific facts you've told it to remember.
- **Accumulate context on long-running work.** You can create a named "Project" (a research topic, an ongoing piece of work — anything) and AuraAgent will keep a running, compact summary of where things stand, so picking a project back up next week doesn't mean starting from zero. More in Chapter 7.
- **Run tasks on a schedule.** Ask it once, in plain language, and it'll run again later on its own — once at a specific time, or repeatedly (hourly, daily, weekly on specific days, monthly, yearly) — with no one needing to be there when it fires. More in Chapter 8.
- **Learn new skills, with your approval.** If nothing built-in can do what you're asking, AuraAgent can write itself a small, single-purpose tool, search a public index of ready-made tools, or connect to a well-known external service — but only after showing you exactly what it wants to do, in full, and getting your go-ahead.

Out of the box, AuraAgent comes with a small starter team: an **orchestrator** who handles your requests directly and delegates to two specialists — a **researcher** (web research, calculations, market research) and a **scheduler** (calendar and to-do management). You can add more team members later, either by asking AuraAgent to propose one itself or by configuring one directly.

---

## Chapter 3 — How It "Thinks," With an Analogy

Picture AuraAgent as a tiny company, and you're the owner.

There's an **office manager** — this is the "orchestrator" — who greets every request you make. For anything simple, they just handle it themselves on the spot. For anything that calls for a specialist, they hand it off to the right person on the team: a **researcher** who's good at digging up information, or a **scheduler** who keeps the calendar and to-do list straight — and bring the result back to you.

Here's the important part: this office has **glass walls**. You can watch the manager think out loud, see exactly when and why they hand a task to a specialist, and watch that specialist's own work happen in real time, nested right inside the manager's own running notes. If two specialists are working on different parts of your request at once, you see both of them working in parallel, not a confusing jumble.

And unlike a normal office, this one can grow — recruiting new specialists, picking up new tools, even connecting outside vendors — but every single one of those changes has to be laid out for you and approved by you first. Nothing new joins the team, and no outside service gets connected, without your sign-off.

---

## Chapter 4 — Where the Sense of Safety Comes From

AuraAgent doesn't treat every action the same way. It sorts what it's about to do into a handful of risk levels, and how it behaves depends on which level an action falls into:

- **Routine and reversible** (reading a file, looking something up, checking your calendar): it just does it.
- **Could lose or overwrite something** (deleting a file, modifying an important calendar event): it stops and asks you a direct yes/no question first, explaining exactly what would be affected.
- **Exposes something private** (taking a screenshot of your whole screen): it always asks first, and explains plainly that it will capture *everything* currently visible, not just what's relevant to your conversation.
- **Changes what AuraAgent itself is capable of** (writing itself a new tool, recruiting a new team member, connecting to an outside service): this always gets the most thorough review. For a self-written tool, you see the **full source code**, not a summary, before it's ever allowed to run. For a new outside connection, you see the exact command it wants to run and get to decide whether you trust it — and any password or API key it needs is typed directly by *you*, never by the AI, so it never passes through its memory or gets written to a log.

A decline is always a completely normal outcome, never an error. If you say no, AuraAgent just says "okay" and moves on or tries something else — it doesn't get stuck, and it doesn't quietly try again a different way.

---

## Chapter 5 — Getting Started

You'll need Python installed, and an API key from either Anthropic (Claude) or an OpenAI-compatible service (OpenAI itself, or something like DeepSeek).

```bash
# 1. Install dependencies (a virtual environment is recommended)
python -m venv .venv
.venv\Scripts\activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2. Set up your API key
copy .env.example .env
# Open .env in a text editor and fill in:
#   Using Anthropic:  AURA_LLM_PROVIDER=anthropic, then ANTHROPIC_API_KEY=<your key>
#   Using DeepSeek (or another OpenAI-compatible service):
#     AURA_LLM_PROVIDER=openai
#     OPENAI_API_KEY=<your key>
#     OPENAI_BASE_URL=https://api.deepseek.com
#     AURA_MODEL_ID=deepseek-chat

# 3. Run it
python main.py
```

You'll land in a simple prompt where you just type what you want in plain language — "create a note summarizing today's meeting," "look up the latest news on electric vehicles," "what's on my calendar this week." Type `exit` when you're done. As it works, you'll see its thinking, its tool calls, and the results, all printed live.

A graphical desktop version also exists (see [Running the GUI](docs/ARCHITECTURE.md#running-the-gui-m1-m4-backend-frontend-panels-desktop-app) in the technical doc) if you'd rather not use a terminal.

**Want a real Google Calendar instead of the built-in local one?** Type `/calendar connect` after starting AuraAgent — it'll walk you through a one-time browser sign-in. See the [technical doc](docs/ARCHITECTURE.md) for the short Google Cloud Console setup this needs first.

---

## Chapter 6 — A Few Real Examples

A few concrete things you might actually say to it:

> "Look up recent news on AI chips and summarize the biggest developments."
> AuraAgent delegates this to its researcher, who pulls real recent headlines and writes you a synthesized summary — not just a raw list of links.

> "Add a meeting with the design team to my calendar for Thursday at 2pm, and remind me it's important."
> Handled by the scheduler. Since you flagged it important, any later change to that specific event will pause and ask you to confirm first.

> "Summarize this folder of PDFs into a slide deck."
> It can read what's in a folder you point it at, and generate an actual PowerPoint file from a summary — a real deliverable, not just a description of one.

> "I don't have a tool for checking currency exchange rates — can you get one?"
> AuraAgent searches for an existing, ready-made tool that already does this, shows you exactly what it found and what it would connect, and only proceeds once you say yes.

---

## Chapter 7 — Working on Long-Term Projects

Some things you work on aren't a single conversation — they're ongoing. A research topic you keep coming back to. A long-term personal project. Something you want AuraAgent to keep building context on over weeks or months, without you re-explaining the background every single time.

That's what a **Project** is for. You give it a name, and from then on:

```
/project create ai_governance                       # creates a fresh project folder for it
/project use ai_governance                           # enter it — files, research, everything now lands here
/project                                              # see which project is active, and list them all
/project none                                         # step out, back to your regular workspace
```

Once you're inside a project, anything AuraAgent saves — documents, notes, research output — lands in that project's own folder, kept separate from everything else. And every so often, it'll jot down a short, up-to-date summary of where things stand, so the next time you type `/project use ai_governance`, it already remembers the state of things — no need to recap.

Entering a project is just for that session; nothing is selected automatically when you start up, and you're free to switch between projects — or leave one entirely — whenever you like.

In the graphical app, this same idea extends down to individual chats: any chat that isn't part of a Project can be given its own working directory too (its ⋯ menu → "设置工作目录"/set working directory), pointing it at any real folder on your computer — not just for chats inside a Project. A Project's own directory always wins if a chat has both; the directory setting just quietly resumes the moment you take the chat back out of the Project, nothing is lost by moving it in and out.

A Project has grown into more than a folder and a summary. It can carry its own **Role** — a short description of how AuraAgent should behave specifically within that project, drafted by the AI but always yours to rewrite — and its own **Memory**, a set of durable facts scoped to that project alone, kept separate from what it remembers about you in general. You can also let a project load extra tools it wouldn't normally have — a particular MCP server or Skill — so they're only available while that project is active, keeping everything else uncluttered. And if something you're discussing was actually covered in a *different* chat under the same project, AuraAgent can search across that project's other chats to find it, instead of treating every chat as a blank slate.

In the graphical app, a project's own page gives each of these its own tab — Role and Memory, which tools are loaded, and the project's files (upload, rename, delete, download) — so managing any of it is a matter of clicking, not remembering command syntax.

---

## Chapter 8 — Running Tasks on Your Schedule

Some things don't need you to ask every time — they just need to happen: check on something every morning, follow up once at a specific time, keep an eye on a topic every week. You can just ask, in plain language, in any chat — inside a Project's or a completely unrelated one, no special command needed for the ask itself:

> "Every day at 9am, look into what's new with OpenAI and save a short write-up."
> "Remind me about the contract renewal on October 5th at 3pm."

It confirms the details with you before setting anything up — this creates a standing commitment, not a one-off action, so it always asks first. Once approved, it runs on its own at the time(s) you described — once, or repeatedly on whatever schedule you gave it (hourly, daily, specific weekdays, monthly, yearly). Set one up while working inside a Project, and it runs with that Project's own context (files, Role, Memory) already loaded, exactly as if you'd typed the request yourself.

Two things worth knowing about how this works: since no one's there to answer if a step needs your approval mid-task, anything that would normally ask for confirmation is automatically skipped rather than left waiting. And it only actually fires while AuraAgent itself is running (the terminal or the graphical app) — if it was closed when something was due, it catches up the next time you start it rather than silently skipping it.

All your scheduled tasks — across every Project and outside of any Project — live in one place: the **Schedules** panel in the graphical app (or `/schedule` in the terminal), where you can see what's coming up, what already ran and how it went, and pause, edit, or delete any of them.

---

## Chapter 9 — What's Done, and What's Next

AuraAgent has been built incrementally, with each piece fully working and tested before moving to the next. As of today, it can:

- Hold a real conversation, remembering context within a session
- Research the web, do market/industry research on a given topic
- Manage notes, a calendar (local or real Google Calendar), and a to-do list
- Manage files on your computer, including advanced editing tools
- Read, create, and edit Word/PowerPoint/Excel documents, and read text-based PDFs
- Control basic desktop functions (clipboard, screenshots, running programs, notifications)
- Remember durable facts and a structured profile about you across sessions
- Accumulate long-term context inside named Projects
- Run tasks on their own, once or on a schedule, without you present
- Write itself new tools, recruit new team members, and connect to outside services — always with your approval first
- Run either in a terminal, or in a graphical desktop window

The backend can also now safely be reached by more than one device at once, each with its own access token (off by default — a purely local setup is completely unaffected) — groundwork for a future companion mobile app to talk to the same AuraAgent running on your computer, not a feature you'd turn on today without one.

Looking ahead, the current top priority is **handling very long, deep-research tasks more gracefully** — picking up where it left off instead of just failing partway through. Scheduled tasks (Chapter 8) cover the most concrete form of "speaking up on its own" — a standing time-based commitment you set in advance — but AuraAgent still can't decide, entirely on its own judgment and with no time set in advance, that something newly worth mentioning has come up; that more open-ended form of proactivity is a further-out goal, not yet scheduled. Further out still, watched but not yet scheduled: actually *seeing* a screenshot or a document image (not just its embedded text) instead of only knowing where it was saved, and editing PDF layout, not just reading it.

---

## Want the Technical Details?

Everything above is the "what" and the "why." For the "how" — the actual architecture, every module, every abstraction, the full security model, and the complete history of how this was built — see **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)**.
