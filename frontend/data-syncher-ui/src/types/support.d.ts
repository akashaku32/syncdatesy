export interface SupportTicketReply {
  id: number;
  message: string;
  is_support_team: boolean;
  created_at: string;
  created_by: number;
  attachment: string | null;
  attachments: string[];
}

export interface SupportConnectionChoice {
  connection_id: number;
  connection_name: string;
  connector_type: string;
}

export interface SupportEditableField {
  name: string;
  required: boolean;
  type: "PrimaryKeyRelatedField" | "ChoiceField" | "CharField" | "FileField";
  choices?: (string | number)[];
  multiple?: boolean;
}

export interface SupportTicketChoicesResponse {
  source_type: string[];
  categories: CategoryDetail[];
  issue_types: IssueTypeDetail[];
  connections: SupportConnectionChoice[];
  editable_fields: SupportEditableField[];
}

export interface ClosedByUser {
  user_id: number;
  email: string;
  first_name: string;
}

export interface ClosedByAdmin {
  admin_id: number;
  full_name: string;
  email: string;
}

export interface CreateSupportTicketPayload {
  attachment: File | null;
  attachments?: File[];
  category: string;
  connection: string;
  connection_name: string;
  description: string;
  subject: string;
  issue_type: string;
  source_type: string;
}

export interface CreateSupportTicketResponse {
  message?: string;
}

export interface CategoryDetail {
  category_id: number;
  name: string;
  description: string | null;
}

export interface IssueTypeDetail {
  issue_type_id: number;
  category: number;
  category_name: string;
  name: string;
  description: string | null;
}

export interface SupportTicketResponse {
  cmp: number;
  connection: number;
  ticket_id: number;
  ticket_code?: string;
  category: number;
  category_detail: CategoryDetail | null;
  issue_type: number;
  issue_type_detail: IssueTypeDetail | null;
  source_type: string;
  connection_name: string;
  subject: string;
  description: string;
  attachment: string | null;
  attachments: string[];
  status: string;
  created_at: string;
  updated_at: string;
  created_by: number;
  updated_by: number;
  has_new_response: boolean;
  closed_by_user?: ClosedByUser | number | null;
  closed_by_admin?: ClosedByAdmin | number | null;
  closed_by_user_name?: string | null;
  closed_by_admin_name?: string | null;
  closed_at?: string | null;
}
