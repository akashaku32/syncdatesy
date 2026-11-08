export type ErrorResponseType = {
  code: number;
  error: string;
  message: string;
  trial_expired?: boolean;
  redirect_to?: string;
  // Google Drive folder confirmation (HTTP 409)
  requires_confirmation?: boolean;
  folder_name?: string;
};
