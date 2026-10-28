import React from "react";
import type { SessionState } from "../hooks/useSession";
import { Tooltip } from "../primitives/Tooltip";

export function SessionIdentity({
  session,
}: {
  session: Pick<
    SessionState,
    "loading" | "loggedIn" | "displayName" | "signInLabel" | "loopbackHint"
  >;
}): React.ReactElement | null {
  if (session.loading || !session.loggedIn || !session.displayName) return null;
  const tooltip = session.signInLabel ?? session.displayName;
  return (
    <div className="shell-session-identity" data-testid="shell-session-identity">
      <Tooltip content={tooltip} placement="bottom">
        <span className="shell-session-identity__user" aria-label={tooltip}>
          {session.displayName}
        </span>
      </Tooltip>
      {session.loopbackHint ? (
        <p className="shell-session-identity__hint" role="note">{session.loopbackHint}</p>
      ) : null}
    </div>
  );
}
