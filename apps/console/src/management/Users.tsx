import { useQuery } from "@tanstack/react-query";
import { z } from "zod";
import { api, type Session } from "../api";

const userSchema = z.object({
  id: z.string(),
  display_name: z.string(),
  issuer: z.string(),
  subject: z.string(),
  role: z.string(),
  active: z.boolean(),
  auth_revision: z.number(),
  device_grants: z.array(z.string()),
  output_grants: z.array(z.string()),
  operation_grants: z.array(z.string()),
});

export function ManagementUsers({ session }: { session: Session }) {
  const users = useQuery({
    queryKey: ["management", "users"],
    queryFn: () => api("/management/users", z.array(userSchema), session),
  });
  return (
    <section className="panel">
      <h2>관리 사용자</h2>
      {users.isPending ? (
        <p role="status">사용자 조회 중…</p>
      ) : users.data?.length ? (
        <table>
          <thead>
            <tr>
              <th>이름</th>
              <th>역할</th>
              <th>상태</th>
              <th>권한 revision</th>
            </tr>
          </thead>
          <tbody>
            {users.data.map((user) => (
              <tr key={user.id}>
                <td>{user.display_name || user.subject}</td>
                <td>{user.role}</td>
                <td>{user.active ? "active" : "disabled"}</td>
                <td>{user.auth_revision}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p>등록된 외부 관리 사용자가 없습니다.</p>
      )}
    </section>
  );
}
