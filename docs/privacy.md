# Privacy — Runner Dashboard

## Assistant Chat History

The in-app assistant sidebar (`✨ Assistant`) may be used on **shared developer
machines**. To prevent chat transcripts from leaking to the next user, history
persistence is **opt-in and off by default**.

### Behaviour

| Setting | Stored in localStorage? |
|---------|------------------------|
| `Save chat history` OFF (default) | No — transcript is discarded on page unload |
| `Save chat history` ON | Yes — capped at 200 messages, auto-expired after **24 hours** |

### Controls

- **Save chat history toggle** — found in the assistant sidebar settings panel
  (⚙️ icon). Default: off.
- **Clear chat history button** — also in the settings panel. Wipes the stored
  transcript immediately and removes both the `assistant:transcript` and
  `assistant:transcript:ts` localStorage keys.

### localStorage keys

| Key | Purpose |
|-----|---------|
| `assistant:transcript` | Serialised message array (written only when toggle is on) |
| `assistant:transcript:ts` | Unix-ms timestamp of last write, used for 24 h TTL |
| `assistant:saveHistory` | Persists the user's toggle preference across sessions |

### Recommendations for shared boxes

- Leave `Save chat history` **off** (the default).
- If history was previously enabled, click **Clear chat history** before
  handing the machine to another user.
- Operators with stricter requirements can clear `localStorage` in the browser
  developer tools (`Application → Storage → Local Storage → Clear All`).
