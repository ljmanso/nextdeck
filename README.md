# nextdeck

A small Linux desktop app that shows one **Nextcloud Deck** board as due-date
columns, lives in the system tray, and starts with your session.

Deck groups cards by *stack* — the stage of work they're in. That's the wrong
axis for answering "what do I need to do today", so nextdeck re-projects a
single board onto **when things are due**:

```
Overdue │ Today │ Tomorrow │ Next 7 days │ Next 30 days │ Later │ No due date │ Done
```

Drag a card between columns to reschedule it. Drop it on **Done** to complete
it, or drag it back out to reopen it.

It is deliberately quiet: no count badge on the tray icon, no popup
notifications, no red alarm chrome. The Overdue column is there because it's
useful, not to nag you.

## Features

- **Due-date columns** computed locally, so completed cards never show as overdue
- **Add tasks** with a title, Markdown details and an optional due date
- **Click a card to edit it** in a panel beside the board — title, due date and
  details, without leaving the window. Save commits, Escape discards
- **Complete / reopen** from the card, its context menu, the editor, or by
  dragging to Done
- **Delete** with a confirmation (Nextcloud keeps deleted cards in the board trash)
- **Drag to reschedule**, preserving a card's existing time of day
- **Search** across titles, details, labels and stacks, filtering the board
- **Labels** shown in the board's own colours, editable from the card editor,
  and clickable to filter the board down to one of them
- **Choose which columns to show** — hide Done, or keep only the near-term ones.
  The columns you keep share the width, and empty ones collapse out of the way
- **Adjustable font size** and card title line limit
- **Tray resident**: closing the window hides it, left-click brings it back —
  and brings the board up to date, if you want it to
- **Autostart** on login via a standard freedesktop `.desktop` entry
- **In your application menu**, so it launches like any other installed app
- **Remembers its size and position**, so it reopens where you left it
- **YAML config** that the Settings dialog reads and writes for you

## Requirements

- Python 3.10+
- A Nextcloud instance with the **Deck** app enabled
- A **system tray** (StatusNotifier host). KDE, XFCE, Cinnamon and most tiling
  bars have one. GNOME does **not** by default — install the
  [AppIndicator](https://extensions.gnome.org/extension/615/appindicator-support/)
  extension, or nextdeck will notice there is no tray and simply keep its
  window open instead of vanishing on close.
- Qt 6 runtime libraries. PySide6 ships its own Qt, but that Qt links against
  system graphics libraries.

  Arch / CachyOS — most of these come with any desktop install; `xcb-util-cursor`
  is the one Qt 6.5+ needs that is often missing:
  ```bash
  sudo pacman -S --needed python libxkbcommon-x11 xcb-util-cursor \
      xcb-util-image xcb-util-keysyms xcb-util-wm libglvnd fontconfig
  ```

  Debian / Ubuntu:
  ```bash
  sudo apt install python3-venv libgl1 libegl1 libxkbcommon0 \
      libxkbcommon-x11-0 libxcb-cursor0 libdbus-1-3
  ```

Check whether Qt can actually start before going further:

```bash
.venv/bin/python -c "from PySide6.QtWidgets import QApplication, QSystemTrayIcon; \
app = QApplication([]); print('Qt OK, tray available:', QSystemTrayIcon.isSystemTrayAvailable())"
```

## Install

```bash
git clone <this repo> nextdeck && cd nextdeck
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/nextdeck
```

> If this checkout is shared with a container (a bind mount), do not reuse a
> `.venv` built inside it: its interpreter symlinks point at the container's
> paths and its wheels were built for that distro. Use a separate directory on
> the host, e.g. `python -m venv .venv-host`.

To put `nextdeck` on your `PATH` without activating the venv each time:

```bash
pipx install .
```

## First run

The app opens the Settings dialog when it has no configuration.

1. **Create an app password** in Nextcloud: *Settings → Personal → Security →
   Create new app password*. Use this instead of your account password — it's
   scoped, revocable, and it's what ends up in the config file.
2. Enter your server URL (for example `https://cloud.example.org`), your
   username, and that app password.
3. Click **Test connection**. This validates the credentials and fills the
   **Board** dropdown.
4. Pick the board you want, and pick which list new tasks should be added to.
5. **Save**.

## Configuration

`~/.config/nextdeck/config.yaml` (or `$XDG_CONFIG_HOME/nextdeck/config.yaml`).
The Settings dialog writes this file, but it's plain YAML if you'd rather edit
it by hand.

```yaml
server:
  url: https://cloud.example.org
  username: luis
  app_password: "xxxxx-xxxxx-xxxxx-xxxxx-xxxxx"
  verify_ssl: true
board:
  id: 3
  default_stack_id: 7
  title: Work                 # display only, filled in by the picker
  default_stack_title: ToDo   # display only
ui:
  refresh_interval_minutes: 10
  refresh_on_show: true       # also refresh when the window comes into view
  start_minimized: true       # from the second start onwards; see "The first run"
  done_column_days: 14        # how long completed tasks stay in Done
  date_format: "%a %d %b"     # strftime
  font_size_pt: 0             # 0 = whatever the system uses
  card_title_lines: 2         # longer titles are elided with an ellipsis
  compact_empty_columns: true # collapse a column with no cards to its header
  toggle_shortcut: ""         # show/hide key, e.g. "F10"; "" means none
  visible_columns:            # omit one to hide that column
    - overdue
    - today
    - tomorrow
    - next_7
    - next_30
    - later
    - no_due_date
    - done
autostart: true
```

> **On the app password.** It is stored in plain text. The file is created with
> mode `0600`, and nextdeck tightens the permissions again on startup if it
> finds them loosened — but anything running as your user can read it. That's
> why the setup asks for a revocable app password rather than your account
> password.

`date_format` uses [strftime](https://strftime.org/) codes.

### When a save fails

Completing, deleting and rescheduling are applied optimistically: the card
moves at once, and if the server refuses, the move is rolled back and the
reason appears in the status line. Those are one-click actions, so redoing one
costs nothing.

An editor save is different — it carries text you typed. If the write does not
reach the server, nextdeck rolls the card back like any other failure, says so
in a dialog naming the card and the reason, and puts what you typed back into
the editor panel. The work stays on screen, so you can copy it somewhere safe,
press Save to try again, or press Escape to drop it.

Nothing is queued and nothing is retried behind your back: an edit that
silently lands ten minutes later is harder to reason about than one you send
again yourself.

### Refreshing

The board refreshes on a timer (`refresh_interval_minutes`). Because the
window spends most of its life hidden in the tray, it also refreshes whenever
it comes back into view, which is the moment stale data is both most likely
and most obvious. Turn that half off with **Settings → Behaviour and UX → Also
refresh when the window is shown** (`refresh_on_show`) and the timer carries
on alone.

Repeated toggling will not hammer the server: a show-triggered refresh is
suppressed for a few seconds afterwards, and the very first show leaves the
startup refresh to do its job rather than duplicating it.

### Column widths

Columns share whatever width the window gives them rather than sitting at a
fixed size with a gap down the right-hand side, so showing three of the eight
gives each of them three times the room. Growth stops at twice a column's
natural width: past that a card stretches into a banner, with its buttons an
arm's length from its title.

With `compact_empty_columns` on (the default), a column holding no cards
shrinks to just its header instead of reserving a lane it has nothing to put
in, and expands again the moment something lands in it. Collapsed columns are
still drop targets, and every collapsed column opens back up for as long as
you are dragging a card — otherwise "No due date" would be at its smallest
exactly when you are trying to drop onto it. Turn the setting off under
**Settings → Behaviour and UX** if you would rather the layout held still.

## Search

The box on the right of the toolbar (`Ctrl+F`) filters the board as you type.
It matches the title, the details, the labels and the stack name, and every
word you type has to match something, so adding a word narrows the result.

Non-matching cards are hidden rather than dimmed, which keeps the due-date
columns meaningful: you still see *when* the matching work is due. Columns
left empty collapse on their own, and the status bar reads `3 of 6 tasks
shown` so the board never looks mysteriously short.

`Enter` moves the keyboard to the results, `Escape` clears the filter and
hands focus back to the board. A refresh keeps the filter applied — the timer
should not quietly undo what you are looking at.

## Labels

Deck labels come from the board, so the colours you see are the ones you chose
in Nextcloud. nextdeck loads the board's label set at startup and after a
settings change, and shows each card's labels as chips under its title.

Click a chip and the board filters down to that label — the same filter the
search box drives, so the search box fills in with the label's name and
`Escape` clears it.

To change a card's labels, open it and use the toggles in the editor panel: a
filled chip is applied, an outlined one is not. Saving writes only what you
changed, as Deck has separate endpoints for assigning and removing a label
rather than a field on the card.

Two behaviours worth knowing:

- A label the board does not define — one deleted in Nextcloud after a card
  was tagged, say — still shows on the card and is still searchable, but has
  no toggle to turn off. Saving leaves it alone rather than stripping it.
- If the board's labels could not be loaded at all, the editor shows no label
  section and a save leaves every card's labels untouched. An empty toggle set
  is never read as "remove all labels".

## Window state

The window's size and position are remembered between runs, recorded whenever
it closes or hides to the tray and restored on the next start.

That state lives in `~/.local/state/nextdeck/state.yaml` (honouring
`$XDG_STATE_HOME`), not in `config.yaml`. The config file holds your app
password, is written `0600` and is meant to be read and edited by hand;
rewriting it every time a window moves would be a risk taken for no benefit.

The geometry is Qt's own opaque blob, which is the only representation that
handles multi-monitor layouts and maximised windows correctly. If it cannot be
read — a different Qt, a corrupted file, a monitor that has since been
unplugged — nextdeck falls back to its default size rather than opening
somewhere you cannot reach. Delete the file to forget the position.

The same file records that nextdeck has started at least once, which is what
makes the **first run show its window** even when `start_minimized` is on —
see below. Deleting the file makes the next start count as a first run again.

### The first run

`start_minimized` is honoured from the second start onwards. The first one
always opens the window, because an app that goes straight to the tray on a
brand new install is indistinguishable from one that failed to start — and
that includes the case where you drop a finished `config.yaml` in from
another machine, where there is no settings dialog to make it obvious either.

`--tray` still wins, so autostart keeps working the way it is meant to: an
explicit flag beats this, as it beats the saved preference.

## The application menu entry

`pip install` leaves a console script on `PATH` and nothing a launcher can
see, so on startup nextdeck writes
`~/.local/share/applications/nextdeck.desktop` (honouring `XDG_DATA_HOME`) and
installs its icon into your icon theme. It is rewritten only when its contents
would change, so it does not set desktop databases rescanning on every launch.

Two cases it stays out of the way for:

- If a distro package already provides a system-wide entry, nextdeck leaves it
  alone. A user-level file would shadow it, replacing a correct `Exec` with a
  guess.
- `--no-menu-entry` skips it entirely.

The entry launches without `--tray`, because clicking an icon in a launcher is
a request to see the board, and carries `StartupWMClass=nextdeck` so docks
match the running window to the launcher instead of showing a second,
unlabelled icon. It is independent of the autostart checkbox: turning autostart
off does not remove it.

## Command line

```
nextdeck                  # open the board window
nextdeck --tray           # start hidden in the tray (what autostart uses)
nextdeck --window         # force the window open, ignoring start_minimized
nextdeck --config PATH    # use an alternate config file
nextdeck --no-menu-entry  # skip installing the application menu entry
nextdeck --replace        # start even if another nextdeck is already running
nextdeck --toggle         # show a running nextdeck, or hide it if it is up
nextdeck --check-hotkey   # report how the global show/hide shortcut is set up
nextdeck --register-hotkey  # register the configured key with GNOME or KDE
nextdeck --verbose        # debug logging
```

### One instance at a time

nextdeck lives in the tray, so a second launch — from the menu, a dock, or a
terminal — would otherwise mean two tray icons and two refresh timers on one
board. Instead the second launch asks the running instance to show its window
and exits, which makes the launcher icon behave like a "show me the board"
button.

Instances are keyed by user *and* config file, so `--config work.yaml` and
`--config home.yaml` are deliberately separate apps. `--replace` starts
anyway, and a socket left behind by a crash is cleared rather than blocking
the next start.

## Keyboard

The whole board works without a mouse. Press `F1` in the app for the same
list.

**On the board**, acting on the focused card:

| Shortcut | Action |
|---|---|
| `↑` `↓` | Move between cards in a column |
| `←` `→` | Move between columns, skipping empty ones |
| `Home` / `End` | First / last card in the column |
| `Enter` | Open the card for editing |
| `Space` | Mark complete, or reopen |
| `Delete` | Delete the card (with the usual confirmation) |
| `Ctrl+←` `Ctrl+→` | Move the due date by a day |
| `Shift+Ctrl+←` `→` | Move the due date by a week |

**In the editor:**

| Shortcut | Action |
|---|---|
| `Ctrl+Enter` | Save the open task and close the editor |
| `Escape` | Discard changes and close the editor |

**Anywhere:**

| Shortcut | Action |
|---|---|
| `Ctrl+N` | Add task |
| `Ctrl+F` | Jump to the search box |
| `F5` | Refresh |
| `F1` | Show the shortcut list |
| `Ctrl+W` | Hide to tray |
| `Ctrl+Q` | Quit |
| *(your choice)* | Show or hide the window — off until you set one, see below |

### Showing and hiding the window

**There is no show/hide key until you set one.** Choose it under
**Settings → Behaviour and UX → Show / hide window**, or set
`ui.toggle_shortcut` in the config file the way Qt spells a key (`F10`,
`Ctrl+Shift+D`). Clearing it switches the feature off, and so does a value
nextdeck cannot parse — a typo disables the shortcut rather than binding some
key you did not choose.

The reason it is off by default is that one key is not enough. A Qt shortcut
only reaches nextdeck **while nextdeck has the keyboard**, so on its own it
dismisses a window in front of you but can never bring one back out of the
tray — by then the key is going to whatever app is in front. A key that works
from anywhere has to be owned by the desktop.

So when you pick a key, nextdeck offers to register it with your desktop too:

| Desktop | What nextdeck writes |
|---|---|
| **GNOME** (also Unity, Cinnamon) | A custom shortcut in GSettings, under `org.gnome.settings-daemon.plugins.media-keys` |
| **KDE Plasma** | `nextdeck-toggle.desktop` in `~/.local/share/applications`, plus a section in `~/.config/kglobalshortcutsrc` |
| Anything else | Nothing — see below |

It always asks first, because that is your desktop configuration and not
nextdeck's to change uninvited. Saying no is remembered, so the same key is
not raised again every time you visit Settings; choosing a different key asks
afresh. Clearing the key removes the binding without asking — leaving a live
global shortcut behind after you turned the feature off would be a bug, not
caution. Existing shortcuts are never disturbed: nextdeck rewrites only the
one entry it owns.

What is registered is checked against what is configured on every save, not
just when you change the key. A key can sit in `config.yaml` having never
been registered — set before this feature existed, or copied in from another
machine — and that is a state nothing would otherwise ever fix.

On KDE, the shortcut daemon is stopped for the moment the config file is
written and started again straight after. That is not politeness, it is the
only way the edit survives: `kglobalaccel` keeps every shortcut in memory and
writes the whole file back when it saves, so a section added underneath a
running daemon is simply discarded.

On Plasma 6, and on Wayland in particular, that daemon is not a process of its
own any more — `kglobalaccel` is part of KWin, so `plasma-kglobalaccel.service`
does not exist and stopping it answers *Unit not loaded*. KWin reads the
shortcut file only when it starts and offers nothing that reloads it, which
leaves one honest conclusion:

> **On Plasma 6, a newly registered key does not work until you log out and
> back in.** Everything is written correctly before then; it is simply not
> loaded.

nextdeck says so rather than leaving you to find out — a dialog when you
register the key from Settings, a line from `--register-hotkey`, and a verdict
from `--check-hotkey`. It also asks KWin for a configuration reload on the way
past (`org.kde.KWin.reconfigure`, through whichever of `qdbus6`, `qdbus-qt6`,
`qdbus` or `gdbus` is installed) in case some version does pick shortcuts up
that way, but that call is not treated as success: there is no evidence it
reloads them, and claiming a dead key is live is what sends you hunting for a
conflict that does not exist.

On Plasma 5, where the daemon *is* its own process, restarting it loads the new
binding immediately and no re-login is needed. GNOME never had the problem:
GSettings is watched, so a write is in effect as it is made.

**On any other desktop** — Hyprland, sway, i3, XFCE, a bare window manager —
bind the command yourself. The settings dialog shows the exact one, with a
**Copy** button beside it, and the `F1` shortcut list repeats the advice:

```
nextdeck --toggle
```

Window up, it hides; window hidden, it comes back and takes focus; nextdeck
not running at all, it starts with the window open. When nextdeck is
already running, `--toggle` hands the request over with the standard library
alone and exits without importing Qt. Going through Qt cost about 730 ms per key
press, nearly all of it importing PySide6 and the UI just to write seven bytes
to a socket. The fast path takes about 100 ms. Qt is still imported when
nothing answers, because then there really is an app to start. Use the command the
dialog shows rather than typing that line: a shortcut defined in a desktop
environment runs with a minimal `PATH`, so a bare name usually will not
resolve to a venv or a `pip install --user` script, and the binding fails
silently. The command shown is absolute, and carries `--config` when you run
a non-default config file — the toggle socket is keyed by config path, so
without it the request would reach a different instance.

In Hyprland that is `bind = , F10, exec, <command>`; in i3 or sway,
`bindsym F10 exec <command>`.

### When the key does nothing

```
nextdeck --check-hotkey
```

walks the whole chain and says where it breaks: the desktop it detects, the
command it would bind, whether the files are there, which key is actually
registered, and whether a running nextdeck answers on its socket. It sends a
toggle as it goes, so if the window changes state while you run it, everything
below the desktop binding is working and the problem is the binding itself.

It ends with a verdict. The common one is a key set in `config.yaml` that the
desktop knows nothing about, which this fixes without going through Settings:

```
nextdeck --register-hotkey
```

On KDE it also reports two things the file alone cannot tell you: whether the
live session has actually loaded the binding (`kglobalaccel knows it`), and
whether anything else in `kglobalshortcutsrc` claims the same key (`Key also
claimed by`). The second matters because KDE will not say which of two
shortcuts lost a fight over a key, and its settings page only warns about a
clash while you are editing one — so a key something else already holds looks
exactly like a key that was never registered.

If the key *is* registered and still does nothing, work down this list:

1. **Log out and back in**, if you have not since registering it. On Plasma 6
   that is not a ritual, it is the mechanism: see above.
2. **Look for "nextdeck: show or hide" in System Settings → Keyboard →
   Shortcuts** (search for `nextdeck`). Listed means the session has the
   binding and the key itself is the problem; not listed means it still has
   not been loaded.
3. **Try a different key.** F-keys and unmodified keys get claimed by all
   sorts of things; `Meta+Shift+N` is far less likely to be taken.
4. **Search your application launcher for "nextdeck: show or hide".** That is
   the same desktop entry KWin looks up to find the command to run, so if the
   launcher cannot find it either, the entry is not being indexed.

The `.desktop` file nextdeck writes is deliberately *not* marked
`NoDisplay=true`, even though Plasma's own generated command shortcuts are:
an entry hidden that way cannot be found by the shortcuts settings page, which
then errors out instead of listing it
([KDE bug 504232](https://bugs.kde.org/show_bug.cgi?id=504232)) — a binding you
can neither see nor manage. The cost is one extra entry in the application
menu, which is the lesser problem of the two.

### Nudging a due date

`Ctrl+←`/`→` moves the due date by a day rather than hopping to the next
column, so a card often stays where it is — the point is precision, not
column-hopping. The card's time of day is kept, so a 09:00 meeting pushed to
tomorrow is still at 09:00, and repeated presses accumulate rather than
recomputing from the last saved date.

A card with **no due date** is treated as due today, which makes the same two
keys the quickest way to give something a date. Dates can be moved freely into
the past: stepping back a day at a time is deliberate and precise, which is
why it is allowed even though dropping a card onto Overdue is still refused —
a drop there would pick no particular past date.

Focus lands on the first card when the board first loads, follows a card that
changes column, survives a refresh, and returns to the board when the editor
closes. Later refreshes never steal focus from whatever you are using.

Wherever the focus goes, the board follows it: the columns scroll sideways and
the column itself scrolls down, so the focused card is always on screen. That
matters most after a nudge, which can throw a card into a column that is
scrolled somewhere else entirely.

Clicking a card opens the editor; dragging one never does. Saving is always
explicit — **Save** or `Ctrl+Enter`. **Cancel** and `Escape` throw the edits
away, and so does clicking a different card, which the status bar reports so a
mistaken Escape is visible rather than a silent loss.

## How it works

| Module | Responsibility |
|---|---|
| `config.py` | YAML load/save, `0600` enforcement, XDG paths |
| `api.py` | `DeckClient` — all HTTP. No Qt. |
| `model.py` | `Task`, `Bucket`, bucketing and reschedule rules. No Qt. |
| `sync.py` | `SyncController` / `SyncWorker` — HTTP on a background thread |
| `ui/` | Window, columns, cards, editor panel, dialogs, tray |
| `ui/elided_label.py` | Card title: bounded lines, top aligned, elided |
| `autostart.py` | The `.desktop` entry and icon installation |

A refresh is a **single** HTTP request: `GET /boards/{id}/stacks` returns each
stack with its cards embedded. Writes are optimistic — the card moves in the UI
immediately, and rolls back with an explanation in the status bar if the server
rejects the change.

### Deck API quirks worth knowing

All four were measured against **Nextcloud 35.0.2 / Deck 1.19.1**.

1. **The API's `overdue` field ignores `done`.** A completed card whose due date
   has passed is still reported as overdue
   ([deck#8001](https://github.com/nextcloud/deck/issues/8001)) — confirmed live:
   a card marked done came back with `overdue: 2`. nextdeck therefore computes
   every bucket itself from `duedate` and `done`, and never reads `overdue`.

2. **`owner` is asymmetric, and required.** A `GET` returns it as a user
   *object*:

   ```json
   "owner": {"primaryKey": "prueba", "uid": "prueba", "displayname": "ddd", "type": 0}
   ```

   but `PUT` demands a uid *string*. Naive read-modify-write — feed the card
   back as you received it — fails with **HTTP 400**, and so does omitting
   `owner` altogether. `update_card_raw` reduces whatever it is given to a uid
   string, falling back to the authenticated username. This is almost certainly
   the real cause of the widely reported "Deck 1.19 rejects JSON bodies on the
   card PUT": with `owner` sent correctly, the JSON `PUT` works fine.

3. **Form-encoded updates need `0`/`1`, not `false`/`true`.** PHP casts the
   non-empty string `"false"` to boolean *true*, so a fallback that sent
   `archived=false` silently **archived the card** — it vanished from the board
   into the archive. Booleans now go out as `"0"`/`"1"`.

4. **Labels live outside the card body, and `assignLabel` does not dedup.**
   A label is attached and detached through `PUT .../cards/{id}/assignLabel`
   and `/removeLabel` with `{"labelId": n}`; `labels` sent in a card `PUT` is
   ignored, which is why a rename leaves a card's labels alone. Assigning a
   label a card *already has* is accepted with no error and attaches it a
   second time, so the card then reports the same label twice. Removing a
   label a card does not have is a silent no-op, and one `removeLabel` clears
   every copy. nextdeck sends only the difference between the labels before
   and after an edit, so it never creates a duplicate itself, and
   `task_from_card` de-duplicates on the way in so a card duplicated elsewhere
   still renders one chip.

Because of (2), card updates are read-modify-write over the *complete* writable
field set, so no field is ever dropped. The form-encoded retry remains as a
safety net for other Deck versions, but on 1.19.1 it never fires.

## Development

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q                            # 208 tests
```

The UI tests need a Qt that can load, but not a display — they run under
`QT_QPA_PLATFORM=offscreen`. Set it explicitly if your environment doesn't
already:

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest -q
```

`tests/test_ui_smoke.py` skips itself (rather than erroring) when PySide6 can't
load at all, so the logic suite still runs on machines without Qt's graphics
libraries. The UI tests drive a real `MainWindow` against a stub controller and
assert which column each card lands in, that optimistic moves roll back on
error, and that the drag MIME payload round-trips through a real `QDropEvent`.

`test_elided_label.py` also asserts **rendered geometry** — that a card is tall
enough for the lines its title actually uses. Logic-only tests passed happily
while wrapped titles were visibly clipped, so those assertions exist because
that bug shipped once.

### Test layout

| File | Covers | Needs Qt |
|---|---|---|
| `test_config.py` | YAML round-trip, `0600` permissions, URL normalisation | no |
| `test_model.py` | Every bucket boundary, done-precedence, reschedule rules | no |
| `test_api.py` | `DeckClient` over mocked HTTP, incl. the `PUT` fallback | no |
| `test_integration.py` | HTTP → `Task` → columns, on doc-shaped Deck JSON | no |
| `test_autostart.py` | `.desktop` generation, icon install, idempotency | no |
| `test_ui_smoke.py` | Window, columns, cards, drag-drop, tray | yes |
| `test_sync.py` | Worker results must reach the GUI thread | yes |
| `test_ui_dialogs.py` | Icon fallback, drag highlight, add/settings dialogs | yes |
| `test_elided_label.py` | Title line limits, elision, **rendered card geometry** | yes |
| `test_editing.py` | Card clicks, editor panel, visible columns, font size | yes |

### Developing in the dev container

`/workspace/Containerfile` installs the Python toolchain and the Qt runtime
libraries, and defaults `QT_QPA_PLATFORM=offscreen`, so the full suite runs in
the container with no extra setup. Two things to know:

- **Packages must be added to the Containerfile and the image rebuilt.** `sudo`
  cannot work at runtime under `podman run --userns=keep-id`, because the UID
  remapping strips root ownership and the setuid bit from `/usr/bin/sudo`.
- To actually *see* the window rather than test it offscreen, run the container
  with `ENABLE_X11=1 ./run.sh`, which forwards your X11 socket and switches Qt
  to the `xcb` platform plugin.

## License

[0BSD](LICENSE) (Zero-Clause BSD): use, copy, modify and distribute it for any
purpose, with or without fee. Nothing is asked of you in return, not even
keeping the copyright notice. It comes with no warranty.

## How this was written

nextdeck was written with substantial help from an AI coding assistant
(Claude). I directed the design, tested it, and maintain it.
