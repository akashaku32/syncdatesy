import React, {
  startTransition,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  Box,
  Button,
  Field,
  Flex,
  Input,
  NativeSelect,
  Portal,
  Select,
  Text,
  Textarea,
  VStack,
  createListCollection,
} from "@chakra-ui/react";

import { IoMdArrowBack } from "react-icons/io";
import { MdOutlineSave } from "react-icons/md";

import { PasswordInput } from "@/components/ui/password-input";
import { type FieldConfig, type KeyPair } from "@/types/form";

import KeyPairGenerator from "./KeyPairGenerator";
import ProxyAgentGenerator from "./ProxyAgentGenerator";
import {
  isJwtBearerAuth,
  isSalesforceConnector,
  isSnowflakeConnector,
  isSnowflakeKeyPairAuth,
  usesKeyPairGenerator,
} from "./helpers";

const SCHEMA_VALIDATION_MESSAGE =
  "Invalid schema name. Schema name shouldn't be empty, should contain only letters, numbers, or underscores, and cannot begin with a number.";

const validateSchemaName = (value: string): string => {
  const trimmed = value.trim();
  const schemaRegex = /^[A-Za-z_][A-Za-z0-9_]*$/;
  return schemaRegex.test(trimmed) ? "" : SCHEMA_VALIDATION_MESSAGE;
};

type FormConfig = {
  fields: FieldConfig[];
};

interface DynamicFormProps {
  config: FormConfig;
  onSubmit: (
    _values: Record<string, string>,
    _files?: Record<string, File | null>,
  ) => void;
  loading?: boolean;
  defaultValues?: Record<string, string>;
  handleBackButtonClick?: () => void;
  mode?: "create" | "edit";
  destinationName?: string;
  destinationType?: string;
  sourceName?: string;
  hideSubmitButton?: boolean;
  leftButtons?: React.ReactNode;
  rightButtons?: React.ReactNode;
  onValuesChange?: (_values: Record<string, string>) => void;
}

const DynamicForm: React.FC<DynamicFormProps> = ({
  config,
  onSubmit,
  defaultValues,
  loading,
  handleBackButtonClick,
  mode,
  destinationName,
  destinationType,
  sourceName,
  hideSubmitButton = false,
  leftButtons,
  rightButtons,
  onValuesChange,
}) => {
  const isSchemaVisible = (
    field: FieldConfig,
    current: Record<string, string>,
  ) => {
    const selectedAuthType =
      current["auth_type"] ||
      current["authentication_type"] ||
      current["authenticationType"];

    if (
      field.name === "client_secret" &&
      selectedAuthType === "client_certificate"
    ) {
      return false;
    }

    if (
      (field.name === "client_certificate_file" ||
        field.name === "certificate_password") &&
      selectedAuthType !== "client_certificate"
    ) {
      return false;
    }

    const connectorName = (sourceName || destinationName || "").toLowerCase();
    const isSalesforceConnector = connectorName.includes("salesforce");

    if (isSalesforceConnector) {
      if (field.name === "CLIENT_SECRET" && selectedAuthType === "jwt_bearer") {
        return false;
      }
      if (
        (field.name === "integration_username" ||
          field.name === "private_key" ||
          field.name === "passphrase") &&
        (!selectedAuthType || selectedAuthType === "oauth")
      ) {
        return false;
      }
    }

    if (field.name === "destination_schema") {
      const normalizedDstType = (destinationType || "")
        .toLowerCase()
        .replace(/[\s\-._]/g, "");
      const normalizedDstName = (destinationName || "")
        .toLowerCase()
        .replace(/[\s\-._]/g, "");
      if (
        normalizedDstType === "googledrive" ||
        normalizedDstType === "sftp" ||
        normalizedDstType === "amazons3" ||
        normalizedDstType === "azuredatalakestorage" ||
        normalizedDstType === "sharepoint" ||
        normalizedDstName === "googledrive" ||
        normalizedDstName === "sftp" ||
        normalizedDstName === "amazons3" ||
        normalizedDstName === "azuredatalakestorage" ||
        normalizedDstName === "sharepoint"
      ) {
        return false;
      }
    }

    const visibilityAnyOf = field.visibility_any_of;
    const visibilityAllOf = field.visibility_all_of;

    if (visibilityAnyOf && visibilityAnyOf.length > 0) {
      const anyMatch = visibilityAnyOf.some((rule) => {
        const dependentValue = String(current[rule.depend_on] || "").trim();
        const requiredValue = String(rule.dependency || "").trim();
        return dependentValue === requiredValue;
      });
      if (!anyMatch) {
        return false;
      }
    }

    if (visibilityAllOf && visibilityAllOf.length > 0) {
      const allMatch = visibilityAllOf.every((rule) => {
        const dependentValue = String(current[rule.depend_on] || "").trim();
        const requiredValue = String(rule.dependency || "").trim();
        return dependentValue === requiredValue;
      });
      if (!allMatch) {
        return false;
      }
    }

    if (
      (visibilityAnyOf && visibilityAnyOf.length > 0) ||
      (visibilityAllOf && visibilityAllOf.length > 0)
    ) {
      return true;
    }

    const dependOn = field.depend_on ?? null;
    const dependencyValue =
      field.dependency_value ?? (field as FieldConfig).dependency ?? null;

    const hasDependency =
      !!dependOn && dependencyValue !== null && dependencyValue !== undefined;

    // Permanently hidden fields (no conditional reveal).
    if (field.is_visible === false && !hasDependency) {
      return false;
    }

    // Only apply schema-visibility rules when a dependency is explicitly present.
    // This avoids changing behavior for existing connectors that don't use it.
    if (hasDependency && dependOn) {
      const dependentValue = String(current[dependOn] || "").trim();
      const requiredValue = String(dependencyValue || "").trim();
      return dependentValue === requiredValue;
    }

    return true;
  };

  const initialValues = config.fields.reduce(
    (acc, field) => ({
      ...acc,
      [field.name]: defaultValues?.[field.name] ?? "",
    }),
    { ...(defaultValues || {}) } as Record<string, string>,
  );

  const [values, setValues] = useState<Record<string, string>>(initialValues);
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [keyMode, setKeyMode] = useState<"generate" | "manual">("generate");

  const connectorName = sourceName || destinationName;
  const selectedAuthType =
    values["auth_type"] ||
    values["authentication_type"] ||
    values["authenticationType"] ||
    "";
  const hasPassphraseField = config.fields.some((f) => f.name === "passphrase");
  const showKeyPairGenerator = usesKeyPairGenerator(
    connectorName,
    selectedAuthType,
    hasPassphraseField,
  );

  const valuesRef = useRef(values);
  const defaultValuesSerializedRef = useRef<string | null>(
    defaultValues ? JSON.stringify(defaultValues) : null,
  );
  const isDirtyRef = useRef(false);

  useEffect(() => {
    valuesRef.current = values;
    onValuesChange?.(values);
  }, [values, onValuesChange]);

  useEffect(() => {
    if (!defaultValues) return;

    const incoming = JSON.stringify(defaultValues);
    if (incoming === defaultValuesSerializedRef.current) return;
    defaultValuesSerializedRef.current = incoming;

    if (isDirtyRef.current) return;

    startTransition(() => {
      setValues((prev) => ({ ...prev, ...defaultValues }));
    });
  }, [defaultValues]);

  const handleChange = (
    e: React.ChangeEvent<
      HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement
    >,
  ) => {
    const { name, value } = e.target;

    const field = config.fields.find((f) => f.name === name);
    if (field && mode === "edit" && field.read_only === true) {
      console.warn(`Attempted to change read-only field: ${name}`);
      return;
    }

    // Mark the form as dirty so the defaultValues effect doesn't overwrite
    // the user's in-progress edits during a background refetch.
    isDirtyRef.current = true;

    setValues((prev) => {
      const newValues = { ...prev, [name]: value };
      valuesRef.current = newValues;
      return newValues;
    });

    setErrors((prev) => {
      if (name === "destination_schema") {
        return { ...prev, [name]: validateSchemaName(value) };
      }
      return { ...prev, [name]: "" };
    });
  };

  const validateRequiredFieldsBeforeAgentGen = (): boolean => {
    const newErrors: Record<string, string> = {};
    config.fields.forEach((field) => {
      if (field.name === "proxy_agent") {
        return;
      }
      if (!isSchemaVisible(field, values)) return;
      if (field.required) {
        if (field.name === "client_certificate_file") {
          const isUploaded = !!values["client_certificate_uploaded"];
          if (!isUploaded && !files[field.name]) {
            newErrors[field.name] = `${field.label} is required`;
          }
        } else if (!values[field.name]) {
          newErrors[field.name] = `${field.label} is required`;
        }
      }
      if (field.name === "destination_schema") {
        const schemaError = validateSchemaName(values[field.name] || "");
        if (schemaError) {
          newErrors[field.name] = schemaError;
        }
      }
    });

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      return false;
    }
    return true;
  };

  const handleSubmit = () => {
    const newErrors: Record<string, string> = {};
    config.fields.forEach((field) => {
      if (!isSchemaVisible(field, values)) return;
      if (field.required) {
        if (field.name === "client_certificate_file") {
          const isUploaded = !!values["client_certificate_uploaded"];
          if (!isUploaded && !files[field.name]) {
            newErrors[field.name] = `${field.label} is required`;
          }
        } else if (!values[field.name]) {
          newErrors[field.name] = `${field.label} is required`;
        }
      }
      if (field.name === "destination_schema") {
        const schemaError = validateSchemaName(values[field.name] || "");
        if (schemaError) {
          newErrors[field.name] = schemaError;
        }
      }
    });

    if (Object.keys(newErrors).length > 0) {
      setErrors(newErrors);
      return;
    }

    // Reset dirty flag so the next defaultValues update (fresh server data)
    // is allowed to sync into the form after the successful save.
    isDirtyRef.current = false;

    onSubmit(values, files);
  };

  const hasAuthenticationTypeField = useMemo(
    () =>
      config.fields.some(
        (f) =>
          f.name === "authentication_type" ||
          f.name === "authenticationType" ||
          f.name === "auth_type",
      ),
    [config.fields],
  );

  // Filter out passphrase from sorted fields - it will be rendered separately below KeyPairGenerator
  const sortedFields = useMemo(() => {
    return config.fields.filter((f) => f.name !== "passphrase");
  }, [config.fields]);

  // Get passphrase field separately
  const passphraseField = useMemo(() => {
    return config.fields.find((f) => f.name === "passphrase");
  }, [config.fields]);

  // Extract existing keys from defaultValues for edit mode
  // Handle both snake_case (public_key) and camelCase (publicKey) naming
  const existingKeys = useMemo(() => {
    if (mode !== "edit" || !defaultValues) return null;

    const publicKey =
      defaultValues.public_key ||
      defaultValues.publicKey ||
      values.public_key ||
      values.publicKey;
    const privateKey =
      defaultValues.private_key ||
      defaultValues.privateKey ||
      values.private_key ||
      values.privateKey;

    if (publicKey && privateKey) {
      return {
        publicKey,
        privateKey,
        passphrase: defaultValues.passphrase || values.passphrase || "",
      };
    }

    if (
      isSalesforceConnector(connectorName) &&
      isJwtBearerAuth(selectedAuthType) &&
      privateKey
    ) {
      return {
        publicKey: publicKey || "",
        privateKey,
        passphrase: defaultValues.passphrase || values.passphrase || "",
      };
    }

    return null;
  }, [
    mode,
    defaultValues,
    connectorName,
    selectedAuthType,
    values.public_key,
    values.private_key,
    values.publicKey,
    values.privateKey,
    values.passphrase,
  ]);

  const renderInput = (field: FieldConfig) => {
    let inputType = "text";

    if (field.type === "DateField") {
      inputType = "date";
    } else if (field.type === "DateTimeField") {
      inputType = "datetime-local";
    }

    if (!isSchemaVisible(field, values)) return null;

    // Check if field should be read-only in edit mode
    // read_only: true means non-editable in edit mode
    const isReadOnly = mode === "edit" && field.read_only === true;

    if (field.name === "proxy_agent") {
      return (
        <ProxyAgentGenerator
          key={field.name}
          field={field}
          value={values["proxy_agent"] || ""}
          connectorId={
            values["id"] ||
            values["connector_id"] ||
            defaultValues?.["id"] ||
            defaultValues?.["connector_id"]
          }
          connectorName={
            values["name"] ||
            values["connector_name"] ||
            values["connection_name"] ||
            defaultValues?.["name"] ||
            defaultValues?.["connection_name"] ||
            ""
          }
          isEditMode={mode === "edit"}
          agentName="Datasyncher Agent"
          onValidate={validateRequiredFieldsBeforeAgentGen}
          onChange={(agentUuid) => {
            isDirtyRef.current = true;
            setValues((prev) => ({
              ...prev,
              proxy_agent: agentUuid,
            }));
            setErrors((prev) => ({ ...prev, [field.name]: "" }));
          }}
          disabled={isReadOnly}
          error={errors[field.name]}
        />
      );
    }

    if (field.name === "client_certificate_file") {
      const isUploaded = !!values["client_certificate_uploaded"];
      const filename = values["client_certificate_file"];
      const isRequired = field.required && !isUploaded && !files[field.name];

      return (
        <Field.Root
          key={field.name}
          required={isRequired}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Box display="flex" gap={2} alignItems="center" w="full">
            <Input
              type="file"
              id={field.name}
              name={field.name}
              accept=".pfx,.p12,.pem"
              w="full"
              h={10}
              p={1.5}
              css={{
                "&::file-selector-button": {
                  height: "100%",
                  mr: 2,
                  bg: "gray.100",
                  border: "none",
                  borderRadius: "md",
                  px: 3,
                  fontSize: "sm",
                  cursor: "pointer",
                  transition: "background 0.2s",
                },
                "&::file-selector-button:hover": {
                  bg: "gray.200",
                },
              }}
              onChange={(e) => {
                const file = e.target.files?.[0] || null;
                if (file && file.size > 5 * 1024 * 1024) {
                  setErrors((prev) => ({
                    ...prev,
                    [field.name]: "File size must be less than 5MB",
                  }));
                  e.target.value = "";
                  setFiles((prev) => ({ ...prev, [field.name]: null }));
                  return;
                }
                setErrors((prev) => ({ ...prev, [field.name]: "" }));
                setFiles((prev) => ({ ...prev, [field.name]: file }));
              }}
              readOnly={isReadOnly}
              bg={isReadOnly ? "gray.200 !important" : undefined}
            />
          </Box>
          {isUploaded &&
            !files[field.name] &&
            typeof filename === "string" &&
            filename && (
              <Field.HelperText fontSize="xs" color="gray.600">
                Uploaded file: {filename}
              </Field.HelperText>
            )}
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    const authTypeValue =
      values["auth_type"] ||
      values["authentication_type"] ||
      values["authenticationType"];

    if (hasAuthenticationTypeField) {
      // If the value of authentication_type field is "password",
      // hide private_key, public_key & passphrase fields
      if (
        (field.name === "private_key" ||
          field.name === "public_key" ||
          field.name === "passphrase") &&
        (authTypeValue === "password" ||
          values["authentication_type"] === "password")
      ) {
        return null;
      }
      // If the value of authentication_type field is "keypair",
      // hide password field and passphrase (passphrase will be rendered separately)
      if (
        field.name === "password" &&
        (authTypeValue === "key_pair" ||
          values["authentication_type"] === "key_pair")
      ) {
        return null;
      }
      // Hide passphrase in normal rendering when key generator is shown or key_pair is selected (rendered separately)
      if (
        field.name === "passphrase" &&
        ((showKeyPairGenerator &&
          values["authentication_type"] === selectedAuthType) ||
          authTypeValue === "key_pair")
      ) {
        return null;
      }
      // If the value of authentication_type field is not selected,
      // hide private_key, public_key, passphrase & password fields
      if (
        (field.name === "private_key" ||
          field.name === "public_key" ||
          field.name === "password" ||
          field.name === "passphrase") &&
        !authTypeValue
      ) {
        return null;
      }
    }

    if (
      field.name === "private_key" &&
      values["authentication_type"] === "jwt_bearer"
    ) {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Textarea
            id={field.name}
            name={field.name}
            value={values[field.name] || ""}
            onChange={handleChange}
            placeholder={
              field.placeholder || `Enter ${field.label.toLowerCase()}`
            }
            rows={10}
            fontFamily="monospace"
            fontSize="xs"
            resize="none"
            readOnly={isReadOnly}
            bg={isReadOnly ? "gray.200 !important" : undefined}
            color={isReadOnly ? "black !important" : undefined}
            borderColor={isReadOnly ? "gray.300 !important" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    const usesConnectorKeyFields =
      (isSnowflakeConnector(connectorName) &&
        isSnowflakeKeyPairAuth(
          values["authentication_type"] || authTypeValue,
        )) ||
      (isSalesforceConnector(connectorName) &&
        isJwtBearerAuth(values["authentication_type"] || authTypeValue)) ||
      authTypeValue === "key_pair" ||
      authTypeValue?.toLowerCase().includes("key");

    if (
      (field.name === "private_key" || field.name === "public_key") &&
      usesConnectorKeyFields
    ) {
      if (keyMode === "manual") {
        return (
          <Field.Root
            key={field.name}
            required={field.required}
            invalid={!!errors[field.name]}
          >
            <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
            <Textarea
              id={field.name}
              name={field.name}
              value={values[field.name] || ""}
              onChange={handleChange}
              placeholder={
                field.placeholder || `Enter ${field.label.toLowerCase()}`
              }
              rows={10}
              fontFamily="monospace"
              fontSize="xs"
              resize="none"
              readOnly={isReadOnly}
              bg={isReadOnly ? "gray.200 !important" : undefined}
              color={isReadOnly ? "black !important" : undefined}
              borderColor={isReadOnly ? "gray.300 !important" : undefined}
            />
            {field.description && (
              <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
                {field.description}
              </Field.HelperText>
            )}
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        );
      }
      return null;
    }

    if (field.type === "ChoiceField") {
      const selectedValue = values[field.name];
      const hasOptionDescriptions = (field.choices ?? []).some(
        (opt) => opt.description,
      );

      if (hasOptionDescriptions) {
        const choiceItems =
          field.choices?.map((choice) => ({
            label: choice.display,
            value: choice.value,
            description: choice.description,
          })) ?? [];

        const collection = createListCollection({ items: choiceItems });

        const handleSelectChange = (e: { value: string[] }) => {
          const newValue = e.value[0] || "";
          const syntheticEvent = {
            target: { name: field.name, value: newValue },
          } as unknown as React.ChangeEvent<HTMLSelectElement>;
          handleChange(syntheticEvent);
        };

        return (
          <Field.Root
            key={field.name}
            required={field.required}
            invalid={!!errors[field.name]}
          >
            <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
            <Select.Root
              collection={collection}
              size="sm"
              disabled={isReadOnly}
              value={selectedValue ? [selectedValue] : []}
              onValueChange={handleSelectChange}
              bg={isReadOnly ? "gray.200 !important" : "white"}
            >
              <Select.HiddenSelect id={field.name} name={field.name} />

              <Select.Control>
                <Select.Trigger
                  bg={isReadOnly ? "gray.200 !important" : "white"}
                  color={isReadOnly ? "black !important" : undefined}
                  borderColor={isReadOnly ? "gray.300 !important" : undefined}
                  opacity={isReadOnly ? "1 !important" : undefined}
                  cursor={isReadOnly ? "not-allowed" : undefined}
                >
                  <Select.ValueText
                    placeholder={
                      !field.depend_on
                        ? field.placeholder || "Select option"
                        : undefined
                    }
                  />
                </Select.Trigger>
                <Select.IndicatorGroup>
                  <Select.Indicator />
                </Select.IndicatorGroup>
              </Select.Control>
              <Portal>
                <Select.Positioner>
                  <Select.Content>
                    {collection.items.map((item) => {
                      const choice = field.choices?.find(
                        (c) => c.value === item.value,
                      );
                      return (
                        <Select.Item item={item} key={item.value}>
                          <Flex direction="column" align="flex-start" gap={1}>
                            <Text fontWeight="medium">{item.label}</Text>
                            {choice?.description && (
                              <Text fontSize="xs" color="gray.600">
                                {choice.description}
                              </Text>
                            )}
                          </Flex>
                          <Select.ItemIndicator />
                        </Select.Item>
                      );
                    })}
                  </Select.Content>
                </Select.Positioner>
              </Portal>
            </Select.Root>
            {field.description && (
              <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
                {field.description}
              </Field.HelperText>
            )}
            {errors[field.name] && (
              <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
            )}
          </Field.Root>
        );
      }

      const selectedOption = (field.choices ?? []).find(
        (opt) => opt.value === selectedValue,
      );
      const optionDescription = selectedOption?.description;

      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <NativeSelect.Root size="sm" disabled={isReadOnly}>
            <NativeSelect.Field
              id={field.name}
              name={field.name}
              placeholder={field.placeholder || "Select option"}
              onChange={handleChange}
              value={selectedValue}
              bg={isReadOnly ? "gray.200 !important" : undefined}
              color={isReadOnly ? "black !important" : undefined}
              borderColor={isReadOnly ? "gray.300 !important" : undefined}
            >
              {(field.choices ?? []).map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.display}
                </option>
              ))}
            </NativeSelect.Field>
            <NativeSelect.Indicator />
          </NativeSelect.Root>
          {optionDescription && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {optionDescription}
            </Field.HelperText>
          )}
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    } else if (
      field.type === "PasswordInput" ||
      field.type === "PasswordField" ||
      field.widget === "PasswordInput"
    ) {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <PasswordInput
            id={field.name}
            name={field.name}
            value={values[field.name]}
            onChange={handleChange}
            placeholder={
              field.placeholder || `Enter ${field.label.toLowerCase()}`
            }
            readOnly={isReadOnly}
            bg={isReadOnly ? "gray.200 !important" : undefined}
            color={isReadOnly ? "black !important" : undefined}
            borderColor={isReadOnly ? "gray.300 !important" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    if (field.type === "TextAreaField") {
      return (
        <Field.Root
          key={field.name}
          required={field.required}
          invalid={!!errors[field.name]}
        >
          <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
          <Textarea
            id={field.name}
            name={field.name}
            value={values[field.name] || ""}
            onChange={handleChange}
            placeholder={
              field.placeholder || `Enter ${field.label.toLowerCase()}`
            }
            rows={6}
            resize="vertical"
            readOnly={isReadOnly}
            bg={isReadOnly ? "gray.200 !important" : undefined}
            color={isReadOnly ? "black !important" : undefined}
            borderColor={isReadOnly ? "gray.300 !important" : undefined}
          />
          {field.description && (
            <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
              {field.description}
            </Field.HelperText>
          )}
          {errors[field.name] && (
            <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
          )}
        </Field.Root>
      );
    }

    return (
      <Field.Root
        key={field.name}
        required={field.required}
        invalid={!!errors[field.name]}
      >
        <Field.Label htmlFor={field.name}>{field.label}</Field.Label>
        <Input
          id={field.name}
          name={field.name}
          type={inputType}
          value={values[field.name]}
          onChange={handleChange}
          autoComplete="off"
          placeholder={
            field.placeholder || `Enter ${field.label.toLowerCase()}`
          }
          readOnly={isReadOnly}
          bg={isReadOnly ? "gray.200 !important" : undefined}
          color={isReadOnly ? "black !important" : undefined}
          borderColor={isReadOnly ? "gray.300 !important" : undefined}
        />
        {field.name === "passphrase" && (
          <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
            If a passphrase is provided, the key pair will be generated in
            encrypted form.
          </Field.HelperText>
        )}
        {field.description && (
          <Field.HelperText fontSize="xs" color="gray.600" mt={1}>
            {field.description}
          </Field.HelperText>
        )}
        {errors[field.name] && (
          <Field.ErrorText>{errors[field.name]}</Field.ErrorText>
        )}
      </Field.Root>
    );
  };

  return (
    <form autoComplete="off" style={{ display: "contents" }}>
      <VStack gap={4} align="stretch" maxW="lg">
        {sortedFields.map((field) => {
          const input = renderInput(field);
          if (!input) return null;

          return (
            <React.Fragment key={field.name}>
              <Box>{input}</Box>
              {(field.name === "auth_type" ||
                field.name === "authentication_type" ||
                field.name === "authenticationType") && (
                <>
                  {passphraseField &&
                    (showKeyPairGenerator ||
                      values.authentication_type === "key_pair" ||
                      values.authenticationType === "key_pair" ||
                      values.auth_type === "key_pair") && (
                      <Box key={passphraseField.name}>
                        <Field.Root
                          required={passphraseField.required}
                          invalid={!!errors[passphraseField.name]}
                        >
                          <Field.Label htmlFor={passphraseField.name}>
                            {passphraseField.label}
                          </Field.Label>
                          {passphraseField.type === "PasswordInput" ? (
                            <PasswordInput
                              id={passphraseField.name}
                              name={passphraseField.name}
                              value={values[passphraseField.name] || ""}
                              onChange={handleChange}
                              placeholder={`Enter ${passphraseField.label.toLowerCase()}`}
                            />
                          ) : (
                            <Input
                              id={passphraseField.name}
                              name={passphraseField.name}
                              type="text"
                              value={values[passphraseField.name] || ""}
                              onChange={handleChange}
                              placeholder={`Enter ${passphraseField.label.toLowerCase()}`}
                            />
                          )}
                          {errors[passphraseField.name] && (
                            <Field.ErrorText>
                              {errors[passphraseField.name]}
                            </Field.ErrorText>
                          )}
                        </Field.Root>
                      </Box>
                    )}
                  <KeyPairGenerator
                    formValues={values}
                    mode={mode}
                    destinationName={destinationName}
                    sourceName={sourceName}
                    hasPassphraseField={hasPassphraseField}
                    existingKeys={existingKeys}
                    onKeysGenerated={(keys: KeyPair) =>
                      setValues((prev) => ({
                        ...prev,
                        public_key: keys.publicKey,
                        private_key: keys.privateKey,
                        ...(keys.passphrase && { passphrase: keys.passphrase }),
                      }))
                    }
                    onClearKeys={() =>
                      setValues((prev) => ({
                        ...prev,
                        public_key: "",
                        private_key: "",
                      }))
                    }
                    onModeChange={setKeyMode}
                  />
                </>
              )}
            </React.Fragment>
          );
        })}
        <Flex justifyContent="space-between">
          <Flex gap={4}>
            {handleBackButtonClick && (
              <Button variant="outline" onClick={handleBackButtonClick}>
                <IoMdArrowBack />
                Back
              </Button>
            )}
            {leftButtons}
          </Flex>
          <Flex gap={4}>
            {rightButtons}
            {!hideSubmitButton && (
              <Button
                colorPalette="brand"
                onClick={handleSubmit}
                loading={loading}
                disabled={loading}
              >
                <MdOutlineSave />
                {mode === "create" ? "Create" : "Save"}
              </Button>
            )}
          </Flex>
        </Flex>
      </VStack>
    </form>
  );
};

export default DynamicForm;
