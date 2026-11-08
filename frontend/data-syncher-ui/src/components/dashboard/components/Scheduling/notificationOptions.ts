const EMAIL_RE = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/;

export function parseDirectEmailsInput(value: string): string[] {
  const emails = value
    .split(",")
    .map((email) => email.trim())
    .filter(Boolean);
  const unique: string[] = [];
  const seen = new Set<string>();
  for (const email of emails) {
    if (!EMAIL_RE.test(email)) {
      throw new Error(`Invalid email address: ${email}`);
    }
    const lowered = email.toLowerCase();
    if (seen.has(lowered)) continue;
    seen.add(lowered);
    unique.push(email);
  }
  return unique;
}

export function formatDirectEmailsInput(emails: string[] | undefined): string {
  return (emails ?? []).join(", ");
}
