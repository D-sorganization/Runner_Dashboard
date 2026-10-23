# DeskComputer Crucial X9 Library Source

The X9 source helper defaults to inspection only. It reads Windows volume, partition, and disk metadata through PowerShell, then requires a single volume with the `Crucial X9` label, a Crucial X9 model, USB bus identity, a serial number, a volume ID, and a real WSL mount at `/mnt/<drive-letter>`. It refuses ambiguous or incomplete matches. It never formats a disk, creates a library folder, or starts ingestion.

Before applying, connect the intended X9 and confirm its model and full serial number from its enclosure or device documentation. Run `PYTHONPATH=backend python scripts/register_desk_x9_library.py` to inspect the candidate. The output shows only the last four serial characters and a short fingerprint of the volume ID. Check that those values match the physical device and that the reported library directory already exists. If Windows does not expose the volume inside WSL, or any identity field is missing, stop without registering it.

After physical verification, pass the exact displayed values to the explicit apply command:

```sh
PYTHONPATH=backend python scripts/register_desk_x9_library.py \
  --apply \
  --confirm-serial-suffix '<last-four-serial-characters>' \
  --confirm-volume-fingerprint '<displayed-fingerprint>'
```

The default library directory is `Library` at the root of the mounted X9. Use `--library-subdir` only for a different existing directory beneath that volume. The helper registers source ID `desk-x9-library` as private (`shared=false`); it does not change or replace any Google Drive source. Confirm the result with `PYTHONPATH=backend python -m library sources list`. Do not add a source when more than one X9 matches or the mount identity is uncertain.
