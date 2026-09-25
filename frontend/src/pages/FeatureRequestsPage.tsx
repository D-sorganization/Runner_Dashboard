/**
 * Deprecated module alias: use pages/CodeRequestsPage instead (CR-1, #1281).
 */
import type React from "react";
import { CodeRequestsPage } from "./CodeRequestsPage";

export function FeatureRequestsPage(): React.ReactElement {
  return <CodeRequestsPage />;
}

export default FeatureRequestsPage;
