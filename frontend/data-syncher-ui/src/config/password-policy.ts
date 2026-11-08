const passwordRegex =
  /^(?=.*[A-Z])(?=.*\d)(?=.*[!@#$%^&*()_\-+=[\]{};':"\\|,.<>/?]).{6,}$/;

const passwordPolicyMessage =
  "Password must contain at least one uppercase letter, one number, one special character, and be at least 6 characters long.";

const passwordPolicyErrorMessage =
  "New password must be at least 6 characters long, contain at least one uppercase letter, one number, and one special character";

const passwordPolicy = {
  passwordRegex: passwordRegex,
  passwordPolicyErrorMessage: passwordPolicyErrorMessage,
  passwordPolicyMessage: passwordPolicyMessage,
};

export default passwordPolicy;
