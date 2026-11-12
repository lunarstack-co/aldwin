import type { TodoStatus } from "@/types/protocol";

export type ElementTodoStatus = "pending" | "active" | "done" | "cancelled";

export function mapTodoStatus(status: TodoStatus): ElementTodoStatus {
  switch (status) {
    case "pending":
      return "pending";
    case "in_progress":
      return "active";
    case "done":
      return "done";
    case "dropped":
      return "cancelled";
  }
}

/** The plan shows for the running turn's own updates, or while unfinished work carries over from the last answer. */
export function planVisibility(
  items: readonly { status: TodoStatus }[],
  running: boolean,
  updatedThisTurn: boolean,
  carriesOver: boolean,
): "shown" | "hidden" {
  if (items.length === 0) return "hidden";
  if (running && updatedThisTurn) return "shown";
  const unfinished = items.some((item) => item.status === "pending" || item.status === "in_progress");
  return unfinished && carriesOver ? "shown" : "hidden";
}

/** A plan carries over only from the most recent finished answer, and never from one that was stopped or failed. */
export function planCarriesOver(
  todosTurnId: string | null,
  lastSettled: { id: string; status: string } | undefined,
): boolean {
  // A plan loaded with the conversation belongs to no turn.
  if (todosTurnId === null) return lastSettled?.status !== "stopped" && lastSettled?.status !== "error";
  return lastSettled !== undefined && todosTurnId === lastSettled.id && lastSettled.status === "answered";
}
