export interface EmailGroup {
  id: number;
  name: string;
  email_addresses: string[];
  created_at?: string;
  updated_at?: string;
}

export interface EmailGroupCreate {
  name: string;
  email_addresses: string[];
}

export interface EmailGroupUpdate {
  name: string;
  email_addresses: string[];
}
