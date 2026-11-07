import React, { useState } from "react";
import { StaffPermissionsScreen } from "./StaffPermissions";
import { BoardSettingsScreen } from "./BoardSettings";
import { QueueStepsScreen } from "./QueueSteps";
import { PickupSettingsScreen } from "./PickupSettings";
import { AuditScreen, ResetScreen } from "./Management";

const sections = [
  ["staff", "Staff access", ["booth_lead", "admin"]],
  ["queue", "Queue steps", ["booth_lead", "admin"]],
  ["pickup", "Pickup", ["booth_lead", "admin"]],
  ["board", "Ready board", ["admin"]],
  ["reset", "Reset day", ["booth_lead", "admin"]],
  ["audit", "Audit trail", ["admin"]],
];

// Sub-sections follow the role rules; the API still enforces each permission.
export function SettingsScreen({ role }) {
  const allowed = sections.filter(([, , roles]) => roles.includes(role));
  const [tab, setTab] = useState(allowed[0]?.[0]);
  const current = allowed.some(([id]) => id === tab) ? tab : allowed[0]?.[0];
  return (
    <>
      <div className="category-tabs" role="tablist" aria-label="Settings">
        {allowed.map(([id, label]) => (
          <button
            key={id}
            role="tab"
            aria-selected={current === id}
            className={current === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </div>
      {current === "staff" && <StaffPermissionsScreen />}
      {current === "queue" && <QueueStepsScreen />}
      {current === "pickup" && <PickupSettingsScreen />}
      {current === "board" && <BoardSettingsScreen />}
      {current === "reset" && <ResetScreen />}
      {current === "audit" && <AuditScreen />}
    </>
  );
}
