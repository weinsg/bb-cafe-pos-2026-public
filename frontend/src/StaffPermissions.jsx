import React, { useState } from "react";
import { saveSetting } from "./data";
import { useResource } from "./useResource";
import { Empty, Message } from "./ui";

export function StaffPermissionsScreen() {
  const {
    data: staff,
    connected,
    error,
    reload,
  } = useResource(
    "/admin/staff-permissions",
    "/admin/staff-permissions",
    false,
  );
  const [selected, setSelected] = useState([]);
  const [saving, setSaving] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [failure, setFailure] = useState("");
  // Right: "completed" (Completed orders) or "cancel".
  async function update(ids, enabled, right = "completed") {
    setSaving(true);
    setFeedback("");
    setFailure("");
    try {
      await saveSetting(
        right === "cancel" ? "/admin/cancel-permissions" : "/admin/staff-permissions",
        "PATCH",
        { user_ids: ids, enabled },
      );
      setFeedback(
        `${right === "cancel" ? "Cancel right" : "Completed-order access"} ${enabled ? "enabled" : "disabled"} for ${ids.length} staff account${ids.length === 1 ? "" : "s"}.`,
      );
      setSelected([]);
      await reload();
    } catch (err) {
      setFailure(err.message);
    } finally {
      setSaving(false);
    }
  }
  const accounts = staff || [];
  return (
    <>
      {(error || failure) && <Message error>{failure || error}</Message>}
      {feedback && <Message>{feedback}</Message>}
      <section className="panel table-panel">
        <div className="panel-heading">
          <h2>Staff permissions</h2>
          <span className="muted small">
            Admin manages Leads and Baristas; Leads manage Baristas.
            Control Completed orders and cancellation separately. Admin retains full access.
          </span>
        </div>
        <div className="access-batch">
          <span>{selected.length} selected</span>
          <button
            className="secondary"
            disabled={!selected.length || saving || !connected}
            onClick={() => update(selected, true)}
          >
            Enable selected
          </button>
          <button
            className="secondary"
            disabled={!selected.length || saving || !connected}
            onClick={() => update(selected, false)}
          >
            Disable selected
          </button>
        </div>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Select all manageable staff"
                    disabled={!accounts.length || saving}
                    checked={
                      accounts.length > 0 && selected.length === accounts.length
                    }
                    onChange={(e) =>
                      setSelected(
                        e.target.checked ? accounts.map((u) => u.id) : [],
                      )
                    }
                  />
                </th>
                <th>Staff login</th>
                <th>Completed orders</th>
                <th>Access</th>
                <th>Cancel orders</th>
              </tr>
            </thead>
            <tbody>
              {accounts.map((user) => (
                <tr key={user.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${user.username}`}
                      checked={selected.includes(user.id)}
                      disabled={saving}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, user.id]
                            : selected.filter((id) => id !== user.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    <strong>{user.username}</strong>
                    <small>{user.role === "booth_lead" ? "Booth Lead" : "Barista"}</small>
                  </td>
                  <td>{user.can_view_completed ? "Can view" : "Hidden"}</td>
                  <td>
                    <button
                      className="secondary"
                      disabled={saving || !connected}
                      aria-label={`${user.can_view_completed ? "Disable" : "Enable"} completed access for ${user.username}`}
                      onClick={() =>
                        update([user.id], !user.can_view_completed)
                      }
                    >
                      {user.can_view_completed ? "Disable" : "Enable"}
                    </button>
                  </td>
                  <td>
                    <button
                      className="secondary"
                      disabled={saving || !connected}
                      aria-label={`${user.can_cancel !== false ? "Switch off" : "Switch on"} cancel right for ${user.username}`}
                      onClick={() =>
                        update([user.id], user.can_cancel === false, "cancel")
                      }
                    >
                      {user.can_cancel !== false ? "Can cancel · Switch off" : "Off · Switch on"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!accounts.length && (
          <Empty title={staff ? "No manageable accounts" : "Loading staff…"}>
            Accounts below your role appear here for access management.
          </Empty>
        )}
      </section>
      <Message>
        Permission changes require a connection. A disconnected tablet receives
        changes when it reconnects. Audit history is available only to Admin.
      </Message>
    </>
  );
}
