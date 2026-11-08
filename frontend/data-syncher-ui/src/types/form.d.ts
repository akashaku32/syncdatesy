export type FieldConfig = {
  name: string;
  label: string;
  description?: string;
  help_text?: string;
  placeholder?: string;
  type:
    | "CharField"
    | "ChoiceField"
    | "TextAreaField"
    | "PasswordField"
    | "PasswordInput"
    | "DateField"
    | "DateTimeField";

  /**
   * Optional choices for dropdown fields.
   * Each choice should have a string value and a display.
   */
  choices?: Array<{ value: string; display: string; description?: string }>;
  required: boolean;
  read_only?: boolean; // If true, field will be read-only in edit mode
  widget?: string | null;

  // Optional dynamic visibility (used by some destination schemas like SFTP)
  is_visible?: boolean;
  depend_on?: string | null;
  dependency_value?: string | null;
  // Some schemas use `dependency` instead of `dependency_value`.
  dependency?: string | null;
  /** Show field when any rule matches (OR). */
  visibility_any_of?: Array<{
    depend_on: string;
    dependency: string;
  }>;
  /** Show field only when every rule matches (AND). */
  visibility_all_of?: Array<{
    depend_on: string;
    description?: string;
    dependency: string;
  }>;
};

export interface KeyPair {
  publicKey: string;
  privateKey: string;
  passphrase?: string;
  /** Self-signed X.509 certificate PEM (Salesforce Connected App upload). */
  certificate?: string;
}

export interface KeyPairResponse {
  public_key: string;
  private_key: string;
  passphrase: string;
  exists: boolean;
  publicKey: string;
  privateKey: string;
}

export interface CheckKeyPairRequest {
  cmp_id: number;
  username: string;
  account: string;
}
