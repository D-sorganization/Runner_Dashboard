Serialize memory entry supersession with an atomic SQLite `BEGIN IMMEDIATE` CAS so only one successor is current and losing writers receive a 409 conflict (#3546).
