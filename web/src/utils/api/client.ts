import copy from "@/data/api.json";
import { fill } from "@/utils/text";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

type ErrorBody = { detail?: string | { msg?: string }[] };

function detailOf(body: ErrorBody, status: number): string {
  const { detail } = body;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail.length > 0) {
    return detail.map((item) => item.msg ?? "").filter(Boolean).join(" ");
  }
  return fill(copy.httpStatus, { status });
}

/** A same-origin JSON call to `aldwin web`; failures become `ApiError`. */
export async function call<T>(method: "GET" | "POST", path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, copy.notAnswering);
  }
  const data = (await response.json().catch(() => ({}))) as T & ErrorBody;
  if (!response.ok) throw new ApiError(response.status, detailOf(data, response.status));
  return data;
}
