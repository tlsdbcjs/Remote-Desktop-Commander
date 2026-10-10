import { invoke } from "@tauri-apps/api/core";
import messages from "../messages.json";
type Request = Record<string, unknown> & { action: string };
async function call<T = unknown>(command: string, args: Record<string, unknown> = {}): Promise<T> {
  try { return await invoke<T>(command, args); }
  catch (error) {
    const code = typeof error === "string" ? error : "REQUEST_FAILED";
    const text = messages[code as keyof typeof messages] ?? messages.REQUEST_FAILED;
    throw new Error(text);
  }
}
const request = <T = unknown>(value: Request) => call<T>("client_request", { request: value });
export const clientApi = {
  overview: () => call("client_overview"),
  refresh: () => call("client_refresh"),
  exit: () => call<void>("client_exit"),
  loginSettings: () => call("client_login_settings"),
  setLogin: (enabled: boolean) => call("client_set_login", { enabled }),
  info: () => request({ action: "info" }),
  settings: () => request({ action: "settings" }),
  updateSettings: (value: Record<string, unknown>) => request({ ...value, action: "update_settings" }),
  status: () => request({ action: "status" }),
  start: () => request({ action: "start" }),
  stop: () => request({ action: "stop" }),
  folder: () => call<string>("client_folder"),
  ca: () => call<string>("client_ca"),
  connection: () => call("client_connection"),
  enrollConnection: (value: Record<string, unknown>) => call("client_enroll_connection", { value }),
  enroll: (value: Record<string, unknown>) => request({ ...value, action: "enroll" }),
};

