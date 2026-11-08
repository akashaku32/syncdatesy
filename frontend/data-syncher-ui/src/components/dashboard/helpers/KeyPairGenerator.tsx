import React, {
  startTransition,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";

import { Box, Button, Field, Flex, Text, Textarea } from "@chakra-ui/react";

import { MdContentCopy, MdDownload } from "react-icons/md";

import { toaster } from "@/components/ui/toaster";
import { Tooltip } from "@/components/ui/tooltip";
import type { KeyPair } from "@/types/form";

import {
  checkKeysForUser,
  copyToClipboard,
  deriveCertificateFromPrivateKey,
  downloadTextFile,
  generateKeyPair,
  getKeyPairLookupFields,
  isJwtBearerAuth,
  isSalesforceConnector,
  isSnowflakeKeyPairAuth,
  usesKeyPairGenerator,
} from "./helpers";

interface KeyPairGeneratorProps {
  formValues: Record<string, string>;
  mode?: "create" | "edit";
  destinationName?: string;
  sourceName?: string;
  hasPassphraseField?: boolean;
  onKeysGenerated?: (_keys: KeyPair) => void;
  onClearKeys?: () => void;
  onModeChange?: (_mode: "generate" | "manual") => void;
  existingKeys?: KeyPair | null;
}

const KeyPairGenerator: React.FC<KeyPairGeneratorProps> = ({
  formValues,
  mode = "create",
  destinationName,
  sourceName,
  hasPassphraseField = false,
  onKeysGenerated,
  onClearKeys,
  onModeChange: externalOnModeChange,
  existingKeys,
}) => {
  const connectorName = sourceName || destinationName;
  const passphrase = formValues?.["passphrase"] || "";
  const authenticationType = formValues?.["authentication_type"] || "";
  const { username, account: accountName } = getKeyPairLookupFields(
    formValues,
    connectorName,
  );
  const entityType = sourceName ? "source" : "destination";
  const isSalesforce = isSalesforceConnector(connectorName);

  const shouldShow = usesKeyPairGenerator(
    connectorName,
    authenticationType,
    hasPassphraseField,
  );

  const usesGeneratedKeys =
    isSnowflakeKeyPairAuth(authenticationType) ||
    isJwtBearerAuth(authenticationType);

  const [isGenerating, setIsGenerating] = useState(false);
  const [generatedKeys, setGeneratedKeys] = useState<KeyPair | null>(null);
  const [canGenerate, setCanGenerate] = useState(true);
  const [keyMode, setKeyMode] = useState<"generate" | "manual">("generate");
  const [isNewlyGenerated, setIsNewlyGenerated] = useState(false);

  const passphraseRef = useRef(passphrase);
  const hasGeneratedKeysRef = useRef(false);
  const hasCheckedExistingKeysRef = useRef(false);
  const lastAuthTypeRef = useRef("");
  const lastUsernameRef = useRef("");
  const lastAccountRef = useRef("");
  const prevUsesGeneratedKeysRef = useRef(false);

  const enrichKeysWithCertificate = useCallback(
    (keys: KeyPair): KeyPair => {
      if (!isSalesforce || keys.certificate) {
        return keys;
      }

      const certificate = deriveCertificateFromPrivateKey(
        keys.privateKey,
        username?.trim() || "datasyncher-salesforce-jwt",
        keys.passphrase || passphrase,
      );

      return certificate ? { ...keys, certificate } : keys;
    },
    [isSalesforce, username, passphrase],
  );

  const handleGenerateKeyPair = useCallback(async () => {
    if (keyMode !== "generate") {
      setIsGenerating(false);
      return;
    }

    setIsGenerating(true);
    try {
      const keys = await generateKeyPair(passphrase?.trim() || undefined, {
        includeCertificate: isSalesforce,
        certificateCommonName: username?.trim() || "datasyncher-salesforce-jwt",
      });
      if (keys && keyMode === "generate") {
        const enrichedKeys = enrichKeysWithCertificate(keys);
        setGeneratedKeys(enrichedKeys);
        hasGeneratedKeysRef.current = true;
        setIsNewlyGenerated(true);
        onKeysGenerated?.(enrichedKeys);

        toaster.success({
          title: "Keys generated successfully",
          description:
            mode === "edit"
              ? "New keys have been generated. Click 'Save' to update them in the database."
              : "New RSA key pair has been generated.",
        });
      }
    } catch {
      // Error handled by generateKeyPair
    } finally {
      setIsGenerating(false);
    }
  }, [
    keyMode,
    passphrase,
    onKeysGenerated,
    mode,
    isSalesforce,
    username,
    enrichKeysWithCertificate,
  ]);

  useEffect(() => {
    if (!usesGeneratedKeys || keyMode === "manual") {
      if (!usesGeneratedKeys) {
        lastAuthTypeRef.current = "";
        lastUsernameRef.current = "";
        lastAccountRef.current = "";
        hasCheckedExistingKeysRef.current = false;
        hasGeneratedKeysRef.current = false;
      }
      return;
    }
    if (mode === "edit") {
      return;
    }

    const hasChanged =
      lastAuthTypeRef.current !== authenticationType ||
      lastUsernameRef.current !== username ||
      lastAccountRef.current !== accountName;

    if (
      username?.trim() &&
      accountName?.trim() &&
      (hasChanged || !hasCheckedExistingKeysRef.current)
    ) {
      lastAuthTypeRef.current = authenticationType;
      lastUsernameRef.current = username;
      lastAccountRef.current = accountName;
      hasCheckedExistingKeysRef.current = true;

      checkKeysForUser(username, accountName, authenticationType, entityType)
        .then((keys) => {
          if (keys) {
            hasGeneratedKeysRef.current = true;
            hasCheckedExistingKeysRef.current = true;

            const enrichedKeys = enrichKeysWithCertificate(keys);
            setGeneratedKeys(enrichedKeys);
            setCanGenerate(mode === "create");
            setIsNewlyGenerated(false);
            onKeysGenerated?.(enrichedKeys);
          } else {
            setGeneratedKeys(null);
            hasGeneratedKeysRef.current = false;
            hasCheckedExistingKeysRef.current = true;
            setCanGenerate(true);
          }
        })
        .catch(() => {
          setGeneratedKeys(null);
          hasGeneratedKeysRef.current = false;
          hasCheckedExistingKeysRef.current = true;
          setCanGenerate(true);
        });
    }
  }, [
    authenticationType,
    username,
    accountName,
    keyMode,
    onKeysGenerated,
    entityType,
    mode,
    usesGeneratedKeys,
    enrichKeysWithCertificate,
  ]);

  useEffect(() => {
    if (prevUsesGeneratedKeysRef.current && !usesGeneratedKeys) {
      startTransition(() => {
        setGeneratedKeys(null);
        setCanGenerate(true);
      });
    }
    prevUsesGeneratedKeysRef.current = usesGeneratedKeys;
  }, [usesGeneratedKeys]);

  useEffect(() => {
    if (keyMode === "manual") {
      hasGeneratedKeysRef.current = false;
      startTransition(() => setGeneratedKeys(null));
      return;
    }

    if (
      keyMode === "generate" &&
      existingKeys &&
      !hasGeneratedKeysRef.current
    ) {
      hasGeneratedKeysRef.current = true;
      setIsNewlyGenerated(false);
      startTransition(() => {
        setCanGenerate(true);
        const enrichedKeys = enrichKeysWithCertificate(existingKeys);
        setGeneratedKeys(enrichedKeys);
        onKeysGenerated?.(enrichedKeys);
      });
    }
  }, [existingKeys, onKeysGenerated, keyMode, mode, enrichKeysWithCertificate]);

  const handleModeChange = useCallback(
    (newMode: "generate" | "manual") => {
      if (newMode === "manual") {
        setGeneratedKeys(null);
        hasGeneratedKeysRef.current = false;
        hasCheckedExistingKeysRef.current = false;
        setIsGenerating(false);
        setCanGenerate(true);
        setIsNewlyGenerated(false);
        onClearKeys?.();
      }
      setKeyMode(newMode);
      externalOnModeChange?.(newMode);
    },
    [onClearKeys, externalOnModeChange],
  );

  useEffect(() => {
    if (
      keyMode === "generate" &&
      passphrase &&
      passphraseRef.current !== passphrase &&
      !hasGeneratedKeysRef.current &&
      !hasCheckedExistingKeysRef.current
    ) {
      startTransition(() => setGeneratedKeys(null));
    }
    passphraseRef.current = passphrase;
  }, [passphrase, keyMode]);

  if (!shouldShow) return null;

  const keyUpdateTarget = isSalesforce
    ? "Salesforce Connected App"
    : "Snowflake account";
  const existingKeysLabel = isSalesforce
    ? "integration user and consumer key"
    : "user and account";

  return (
    <Box mt={4}>
      <Flex gap={2} mb={4}>
        <Button
          variant={keyMode === "generate" ? "solid" : "outline"}
          colorPalette={keyMode === "generate" ? "brand" : "gray"}
          onClick={() => {
            if (keyMode !== "generate") {
              handleModeChange("generate");
            } else {
              handleGenerateKeyPair();
            }
          }}
          flex={1}
          loading={isGenerating && keyMode === "generate"}
          disabled={keyMode === "generate" && !canGenerate}
        >
          Generate Keys
        </Button>
        <Button
          variant={keyMode === "manual" ? "solid" : "outline"}
          colorPalette={keyMode === "manual" ? "brand" : "gray"}
          onClick={() => handleModeChange("manual")}
          flex={1}
        >
          Enter Keys Manually
        </Button>
      </Flex>

      {keyMode === "generate" && (
        <>
          {isGenerating && (
            <Text fontSize="sm" color="blue.500" mb={2}>
              Generating keys...
            </Text>
          )}

          {isNewlyGenerated && generatedKeys && (
            <Text fontSize="sm" color="orange.500" mb={2}>
              New keys have been generated. Make sure to update them in your{" "}
              {keyUpdateTarget}.
              {isSalesforce &&
                " Upload the X.509 certificate below to your Connected App under Use digital signatures."}
            </Text>
          )}

          {!isNewlyGenerated &&
            generatedKeys &&
            mode === "create" &&
            hasGeneratedKeysRef.current && (
              <Text fontSize="sm" color="green.500" mb={2}>
                Existing keys found for this {existingKeysLabel}.
              </Text>
            )}

          {generatedKeys && (
            <Flex gap={4} direction="column" mt={4}>
              <Box flex={1}>
                <Field.Root>
                  <Flex
                    justifyContent="space-between"
                    alignItems="center"
                    mb={2}
                  >
                    <Field.Label>Private Key (PEM format)</Field.Label>
                    <Tooltip content="Copy private key">
                      <Button
                        size="xs"
                        variant="ghost"
                        onClick={() =>
                          copyToClipboard(generatedKeys.privateKey, "Private")
                        }
                      >
                        <MdContentCopy size={14} />
                      </Button>
                    </Tooltip>
                  </Flex>
                  <Textarea
                    value={generatedKeys.privateKey}
                    readOnly
                    rows={10}
                    fontFamily="monospace"
                    fontSize="xs"
                    resize="none"
                  />
                </Field.Root>
              </Box>

              {isSalesforce && !generatedKeys.certificate && (
                <Text fontSize="sm" color="orange.500">
                  Could not derive the X.509 certificate. Enter the passphrase
                  and regenerate keys, or use Enter Keys Manually with an
                  unencrypted private key.
                </Text>
              )}

              {isSalesforce && generatedKeys.certificate ? (
                <Box flex={1}>
                  <Field.Root>
                    <Flex
                      justifyContent="space-between"
                      alignItems="center"
                      mb={2}
                    >
                      <Field.Label>
                        X.509 Certificate (upload to Connected App)
                      </Field.Label>
                      <Flex gap={1}>
                        <Tooltip content="Download certificate (.crt)">
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() =>
                              downloadTextFile(
                                generatedKeys.certificate || "",
                                "salesforce-jwt-certificate.crt",
                              )
                            }
                          >
                            <MdDownload size={14} />
                          </Button>
                        </Tooltip>
                        <Tooltip content="Copy certificate">
                          <Button
                            size="xs"
                            variant="ghost"
                            onClick={() =>
                              copyToClipboard(
                                generatedKeys.certificate || "",
                                "Certificate",
                              )
                            }
                          >
                            <MdContentCopy size={14} />
                          </Button>
                        </Tooltip>
                      </Flex>
                    </Flex>
                    <Textarea
                      value={generatedKeys.certificate}
                      readOnly
                      rows={10}
                      fontFamily="monospace"
                      fontSize="xs"
                      resize="none"
                    />
                  </Field.Root>
                </Box>
              ) : (
                <Box flex={1}>
                  <Field.Root>
                    <Flex
                      justifyContent="space-between"
                      alignItems="center"
                      mb={2}
                    >
                      <Field.Label>Public Key (PEM format)</Field.Label>
                      <Tooltip content="Copy public key">
                        <Button
                          size="xs"
                          variant="ghost"
                          onClick={() =>
                            copyToClipboard(generatedKeys.publicKey, "Public")
                          }
                        >
                          <MdContentCopy size={14} />
                        </Button>
                      </Tooltip>
                    </Flex>
                    <Textarea
                      value={generatedKeys.publicKey}
                      readOnly
                      rows={10}
                      fontFamily="monospace"
                      fontSize="xs"
                      resize="none"
                    />
                  </Field.Root>
                </Box>
              )}
            </Flex>
          )}
        </>
      )}

      {keyMode === "manual" && (
        <Text fontSize="sm" color="orange.500" mt={2}>
          Enter your keys manually in the form fields below.
        </Text>
      )}
    </Box>
  );
};

export default KeyPairGenerator;
