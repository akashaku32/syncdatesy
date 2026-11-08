import { type EmailGroup } from "@/types/emailGroups";

export interface SectionStyleState {
  bold: boolean;
  italic: boolean;
  color: string;
  fontFamily: string;
  fontSize: string;
}

export type ActiveSection = "subject" | "greeting" | "body" | "team";

export interface BodyFieldOption {
  id: string;
  label: string;
}

export interface EmailCustomFieldsPayload {
  subject?: string;
  subject_styles?: {
    bold?: boolean;
    italic?: boolean;
    color?: string;
    font_family?: string;
    font_size?: string;
  } | null;
  body_fields?: string[];
  greeting_name?: string;
  greeting_styles?: {
    bold?: boolean;
    italic?: boolean;
    color?: string;
    font_family?: string;
    font_size?: string;
  } | null;
  body_content?: string;
  body_styles?: {
    bold?: boolean;
    italic?: boolean;
    color?: string;
    font_family?: string;
    font_size?: string;
  } | null;
  team_name?: string;
  team_styles?: {
    bold?: boolean;
    italic?: boolean;
    color?: string;
    font_family?: string;
    font_size?: string;
  } | null;
  styles?: {
    bold?: boolean;
    italic?: boolean;
    color?: string;
    font_family?: string;
    font_size?: string;
  } | null;
}

export interface EmailGroupSelectionModalProps {
  open: boolean;
  onClose: () => void;
  tableName: string;
  emailGroups: EmailGroup[];
  initialSelectedGroupIds: number[];
  initialEmailTemplateId?: number | string | null;
  initialEmailCustomFields?: EmailCustomFieldsPayload | null;
  destinationName?: string;
  pathLabel?: string | null;
  onSave: (
    _groupIds: number[],
    _customFields: EmailCustomFieldsPayload,
    _emailTemplateId?: number | string | null,
  ) => void;
  isSaving?: boolean;
  rootFolder?: string | null;
  targetFolder?: string | null;
  outputFileName?: string | null;
  fileFormat?: string | null;
  connectionName?: string;
  companyName?: string;
  sourceDisplayName?: string;
  columnsCount?: number;
}
