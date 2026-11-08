export interface CommunicationSupport {
  email_addresses: string[];
  is_active: boolean;
}

export interface CommunicationSupportUpdate {
  email_addresses_input: string;
  is_active: boolean;
}
