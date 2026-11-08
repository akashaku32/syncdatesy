export interface EmailTemplate {
  id: number | string;
  name: string;
  description?: string | null;
  subject: string;
  header_title?: string | null;
  header_subtitle?: string | null;
  greeting_name?: string | null;
  body_content: string;
  team_name?: string | null;
  cta_button_text?: string | null;
  cta_button_url?: string | null;
  body_fields?: string[] | null;
  // Section Color Themes
  header_color?: string | null;
  header_bg_color?: string | null;
  primary_color?: string | null;
  body_color?: string | null;
  body_bg_color?: string | null;
  button_bg_color?: string | null;
  button_text_color?: string | null;
  button_align?: "left" | "center" | "right" | string | null;
  button_variant?: "solid" | "outline" | "subtle" | string | null;
  footer_color?: string | null;
  // Callout Box Customization
  show_callout_box?: boolean | null;
  callout_content?: string | null;
  callout_styles?: {
    background_color?: string;
    border_color?: string;
    border_width?: string;
    border_radius?: string;
    padding?: string;
    color?: string;
    font_size?: string;
    [key: string]: unknown;
  } | null;
  callout_box_text?: string | null;
  callout_box_bg_color?: string | null;
  callout_box_border_color?: string | null;
  callout_box_text_color?: string | null;
  created_at?: string;
  updated_at?: string;
}

export type CreateEmailTemplatePayload = Omit<
  EmailTemplate,
  "id" | "created_at" | "updated_at"
>;

export type UpdateEmailTemplatePayload = Partial<CreateEmailTemplatePayload>;
