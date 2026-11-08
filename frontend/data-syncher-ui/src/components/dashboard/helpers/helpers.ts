import forge from "node-forge";

import { toaster } from "@/components/ui/toaster";
import ServerRoutes from "@/constants/server-routes";
import AxiosInstance from "@/lib/axios/api-client";
import type {
  CheckKeyPairRequest,
  KeyPair,
  KeyPairResponse,
} from "@/types/form";

const arrayBufferToString = (buffer: ArrayBuffer): string => {
  const uint8Array = new Uint8Array(buffer);
  let binaryString = "";
  for (let i = 0; i < uint8Array.length; i++) {
    binaryString += String.fromCharCode(uint8Array[i]);
  }
  return binaryString;
};

const formatAsPem = (base64: string, label: string): string =>
  `-----BEGIN ${label}-----\n${base64.match(/.{1,64}/g)?.join("\n")}\n-----END ${label}-----`;

const exportPrivateKey = async (
  key: CryptoKey,
  passphrase?: string,
): Promise<string> => {
  const exported = await window.crypto.subtle.exportKey("pkcs8", key);

  if (passphrase && passphrase.trim()) {
    try {
      // Convert PKCS#8 DER to node-forge private key
      const privateKeyDer = forge.util.createBuffer(
        arrayBufferToString(exported),
      );
      const privateKey = forge.pki.privateKeyFromAsn1(
        forge.asn1.fromDer(privateKeyDer),
      );

      // Use node-forge to encrypt with standard PKCS#8 format
      // This generates a standard encrypted private key that the backend can parse
      const encryptedPem = forge.pki.encryptRsaPrivateKey(
        privateKey,
        passphrase,
        {
          algorithm: "aes256", // AES-256-CBC
        },
      );

      return encryptedPem;
    } catch (error) {
      console.error("Error encrypting private key:", error);
      toaster.error({
        title: "Encryption failed",
        description: "Failed to encrypt private key with passphrase.",
      });

      return formatAsPem(
        window.btoa(arrayBufferToString(exported)),
        "PRIVATE KEY",
      );
    }
  }

  // Return unencrypted private key
  return formatAsPem(window.btoa(arrayBufferToString(exported)), "PRIVATE KEY");
};

const exportPublicKey = async (key: CryptoKey): Promise<string> => {
  const exported = await window.crypto.subtle.exportKey("spki", key);
  return formatAsPem(window.btoa(arrayBufferToString(exported)), "PUBLIC KEY");
};

type ForgeRsaPrivateKey = forge.pki.rsa.PrivateKey;

const asRsaPrivateKey = (
  privateKey: forge.pki.PrivateKey,
): ForgeRsaPrivateKey | null => {
  const rsaKey = privateKey as ForgeRsaPrivateKey;
  if (
    rsaKey.n === null ||
    rsaKey.n === undefined ||
    rsaKey.e === null ||
    rsaKey.e === undefined
  ) {
    return null;
  }
  return rsaKey;
};

const loadForgePrivateKey = (
  privateKeyPem: string,
  passphrase?: string,
): ForgeRsaPrivateKey | null => {
  try {
    if (privateKeyPem.includes("ENCRYPTED")) {
      if (!passphrase?.trim()) return null;
      return forge.pki.decryptRsaPrivateKey(privateKeyPem, passphrase.trim());
    }
    return asRsaPrivateKey(forge.pki.privateKeyFromPem(privateKeyPem));
  } catch {
    return null;
  }
};

const createSelfSignedCertificate = (
  privateKey: ForgeRsaPrivateKey,
  commonName: string,
  validityDays = 365,
): string => {
  const cert = forge.pki.createCertificate();
  cert.publicKey = forge.pki.rsa.setPublicKey(privateKey.n, privateKey.e);
  cert.serialNumber = `01${forge.util.bytesToHex(forge.random.getBytesSync(8))}`;

  const notBefore = new Date();
  const notAfter = new Date(notBefore);
  notAfter.setDate(notAfter.getDate() + validityDays);

  cert.validity.notBefore = notBefore;
  cert.validity.notAfter = notAfter;

  const subject = [{ name: "commonName", value: commonName }];
  cert.setSubject(subject);
  cert.setIssuer(subject);
  cert.sign(privateKey, forge.md.sha256.create());

  return forge.pki.certificateToPem(cert);
};

export const deriveCertificateFromPrivateKey = (
  privateKeyPem: string,
  commonName = "datasyncher-salesforce-jwt",
  passphrase?: string,
): string | null => {
  const privateKey = loadForgePrivateKey(privateKeyPem, passphrase);
  if (!privateKey) return null;

  try {
    return createSelfSignedCertificate(privateKey, commonName);
  } catch {
    return null;
  }
};

export type GenerateKeyPairOptions = {
  includeCertificate?: boolean;
  certificateCommonName?: string;
};

export const checkKeyPairInBackend = async (
  username: string,
  accountName: string,
  cmpId: number,
  type: "destination" | "source" = "destination",
): Promise<KeyPair | null> => {
  try {
    const endpoint =
      type === "destination"
        ? ServerRoutes.destination.checkKeyPair()
        : ServerRoutes.source.checkKeyPair();

    const { data } = await AxiosInstance.post<KeyPairResponse>(endpoint, {
      cmp_id: cmpId,
      username,
      account: accountName,
    } as CheckKeyPairRequest);

    const publicKey = data.public_key || data.publicKey;
    const privateKey = data.private_key || data.privateKey;

    if ((data.exists || publicKey || privateKey) && privateKey) {
      return {
        publicKey: publicKey || "",
        privateKey,
        passphrase: data.passphrase,
      };
    }

    return null;
  } catch {
    return null;
  }
};

export const generateKeyPair = async (
  passphrase?: string,
  options: GenerateKeyPairOptions = {},
): Promise<KeyPair | null> => {
  try {
    const keyPair = await window.crypto.subtle.generateKey(
      {
        name: "RSA-OAEP",
        modulusLength: 2048,
        publicExponent: new Uint8Array([1, 0, 1]),
        hash: "SHA-256",
      },
      true,
      ["encrypt", "decrypt"],
    );

    const exportedPkcs8 = await window.crypto.subtle.exportKey(
      "pkcs8",
      keyPair.privateKey,
    );
    const forgePrivateKey = asRsaPrivateKey(
      forge.pki.privateKeyFromAsn1(
        forge.asn1.fromDer(
          forge.util.createBuffer(arrayBufferToString(exportedPkcs8)),
        ),
      ),
    );
    if (!forgePrivateKey) {
      throw new Error("Generated key is not a valid RSA private key");
    }

    let certificate: string | undefined;
    if (options.includeCertificate) {
      certificate = createSelfSignedCertificate(
        forgePrivateKey,
        options.certificateCommonName?.trim() || "datasyncher-salesforce-jwt",
      );
    }

    const [privateKey, publicKey] = await Promise.all([
      exportPrivateKey(keyPair.privateKey, passphrase),
      exportPublicKey(keyPair.publicKey),
    ]);

    return {
      publicKey,
      privateKey,
      passphrase: passphrase || "",
      certificate,
    };
  } catch {
    toaster.error({
      title: "Key generation failed",
      description: "Local key generation failed. Please try again.",
    });
    return null;
  }
};

export const isSnowflakeConnector = (name?: string): boolean =>
  name?.toLowerCase() === "snowflake";

export const isDatabricksConnector = (name?: string): boolean =>
  !!name?.toLowerCase().includes("databricks");

export const isSalesforceConnector = (name?: string): boolean =>
  !!name?.toLowerCase().includes("salesforce");

export const isJwtBearerAuth = (authenticationType: string): boolean =>
  authenticationType === "jwt_bearer";

export const isSnowflakeKeyPairAuth = (authenticationType: string): boolean =>
  authenticationType === "key_pair" ||
  authenticationType?.toLowerCase().includes("key");

export const usesKeyPairGenerator = (
  connectorName: string | undefined,
  authenticationType: string,
  hasPassphraseField: boolean,
): boolean => {
  if (!hasPassphraseField) return false;

  if (
    isSnowflakeConnector(connectorName) &&
    isSnowflakeKeyPairAuth(authenticationType)
  ) {
    return true;
  }

  return (
    isSalesforceConnector(connectorName) && isJwtBearerAuth(authenticationType)
  );
};

export const getKeyPairLookupFields = (
  formValues: Record<string, string>,
  connectorName?: string,
): { username: string; account: string } => {
  const getFieldValue = (names: string[]): string =>
    names.map((name) => formValues?.[name]).find(Boolean) || "";

  if (isSalesforceConnector(connectorName)) {
    return {
      username: getFieldValue(["integration_username"]),
      account: getFieldValue(["CLIENT_ID", "client_id"]),
    };
  }

  return {
    username: getFieldValue(["username", "user_name", "user"]),
    account: getFieldValue([
      "account_name",
      "account",
      "accountName",
      "account_identifier",
    ]),
  };
};

export const checkKeysForUser = async (
  username: string,
  accountName: string,
  authenticationType: string,
  type: "destination" | "source" = "destination",
  suppressMessage: boolean = false,
): Promise<KeyPair | null> => {
  const trimmedUser = username?.trim();
  const trimmedAccount = accountName?.trim();

  const supportsLookup =
    isSnowflakeKeyPairAuth(authenticationType) ||
    isJwtBearerAuth(authenticationType);

  if (!trimmedUser || !trimmedAccount || !supportsLookup) {
    return null;
  }

  const cmpId = await getCompanyId();
  if (!cmpId) return null;

  const existingKeys = await checkKeyPairInBackend(
    trimmedUser,
    trimmedAccount,
    cmpId,
    type,
  );

  if (existingKeys) {
    if (!suppressMessage) {
      toaster.info({
        title: "Keys already exist",
        description: `Keys found for "${trimmedUser}" and "${trimmedAccount}". Using existing keys.`,
      });
    }
    return existingKeys;
  }

  return null;
};

const findFormInput = (names: string[]): HTMLInputElement | null => {
  const form = document.querySelector("form");
  for (const name of names) {
    const selector = (el: Element | Document | null) =>
      el?.querySelector(
        `input[name="${name}"], input[id="${name}"]`,
      ) as HTMLInputElement | null;
    const found = selector(form) || selector(document);
    if (found) return found;
  }
  return null;
};

const getCompanyId = async (): Promise<number | null> => {
  try {
    const { data: user } = await AxiosInstance.get(ServerRoutes.auth.profile());
    const cmpId = user.company?.cmp_id;
    if (!cmpId) {
      toaster.error({
        title: "Failed to get company information",
        description: "Unable to retrieve company ID. Please try again.",
      });
    }
    return cmpId || null;
  } catch {
    toaster.error({
      title: "Failed to get company information",
      description: "Unable to retrieve company ID. Please try again.",
    });
    return null;
  }
};

export const generateKeyPairFromForm = async (): Promise<KeyPair | null> => {
  const passphraseInput = findFormInput(["passphrase"]);
  const usernameInput = findFormInput(["username", "user_name", "user"]);
  const accountNameInput = findFormInput([
    "account_name",
    "account",
    "accountName",
    "account_identifier",
  ]);

  const passphrase = passphraseInput?.value?.trim() || "";
  const username = usernameInput?.value?.trim() || "";
  const accountName = accountNameInput?.value?.trim() || "";

  if (!username || !accountName) {
    toaster.error({
      title: "Missing credentials",
      description: "Username and account name are required.",
    });
    return null;
  }

  const cmpId = await getCompanyId();
  if (!cmpId) return null;

  const existingKeys = await checkKeyPairInBackend(
    username,
    accountName,
    cmpId,
    "destination",
  );

  if (existingKeys) {
    if (existingKeys.passphrase && passphraseInput) {
      passphraseInput.value = existingKeys.passphrase;
    }
    toaster.info({
      title: "Keys already exist",
      description: `Keys found for "${username}" and "${accountName}". Using existing keys.`,
    });
    return existingKeys;
  }

  const newKeys = await generateKeyPair(passphrase || undefined);
  if (!newKeys) {
    toaster.error({
      title: "Key generation failed",
      description: "Failed to generate keys. Please try again.",
    });
    return null;
  }

  toaster.success({
    title: "Keys generated successfully",
    description: `New RSA key pair generated for "${username}" and "${accountName}"`,
  });
  return newKeys;
};

export const copyToClipboard = (
  text: string,
  type: "Public" | "Private" | "Certificate",
): void => {
  navigator.clipboard.writeText(text).then(() => {
    toaster.success({
      title: `${type} copied`,
      description: `${type} copied to clipboard`,
    });
  });
};

export const downloadTextFile = (content: string, filename: string): void => {
  const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
};

export const shouldShowKeyGenerator = (
  mode: "create" | "edit" | undefined,
  destinationName: string | undefined,
  sourceName: string | undefined,
  authenticationType: string,
  hasPassphraseField: boolean,
): boolean => {
  if (mode !== "create") return false;

  return (
    usesKeyPairGenerator(
      destinationName,
      authenticationType,
      hasPassphraseField,
    ) ||
    usesKeyPairGenerator(sourceName, authenticationType, hasPassphraseField)
  );
};
