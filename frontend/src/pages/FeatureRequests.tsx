/**
 * Deprecated module alias: use pages/CodeRequests instead (CR-1, #1281).
 */
import type React from "react";
import { CodeRequestsTab } from "./CodeRequests";
import type { CodeRequestsProps } from "./codeRequestsTypes";

export type * from "./codeRequestsTypes";

export function FeatureRequestsTab(props: CodeRequestsProps): React.ReactElement {
  return <CodeRequestsTab {...props} />;
}

export default FeatureRequestsTab;
