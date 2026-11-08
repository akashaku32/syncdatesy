# © 2026 Spectra Solutions. All rights reserved. For internal use only
# DTP : DTP_00O2TIIO2L73TN9NY6AZRY2PX
# Source : RSDS 0FI_GL_4 (source system FADCLNT100) - LANDING.0FI_GL_4
# Target : ADSO /IMO/CMFIGL04 - LANDING./IMO/CMFIGL04_STRM
# DTP graph : RSDS 0FI_GL_4 -> TRFN 0LTUK2CTVE74FDCWJIZTX29JZRV6ROF6 (ABAP) -> ADSO /IMO/CMFIGL04
# Audit report reference : DTP_00O2TIIO2L73TN9NY6AZRY2PX_audit_report.pdf
# Infoarea : /IMO/FIGL_CM
# Script Generated date : 10/02/2026
# ----------------------------------------------------------------------------
import pandas as pd


def transform(__source_df: pd.DataFrame) -> pd.DataFrame:
    amount_fields = [
        "DMSOL", "DMHAB", "DMSHB", "WRSOL", "WRHAB", "WRSHB",
        "DMSO2", "DMHA2", "DMSH2", "DMSO3", "DMHA3", "DMSH3",
    ]

    # 1. Filter rows: BUKRS in IN01 / 1710 (ignoring trailing spaces)
    mask = __source_df["BUKRS"].str.rstrip(" ").isin(["IN01", "1710"])
    df = __source_df.loc[mask]

    # 2. Overflow check for CURR 13,2 (abs value must be < 10^11)
    amounts = df[amount_fields].apply(pd.to_numeric, errors="coerce")
    overflow = (amounts.abs() >= 10 ** 11).any(axis=1)
    if overflow.any():
        bad = df.loc[overflow, ["BUKRS", "BELNR", "BUZEI", "FISCPER"]].astype(str).head(20)
        raise ValueError(
            "Amount does not fit CURR 13,2 for: "
            + ", ".join("/".join(r) for r in bad.itertuples(index=False))
        )

    # 3. Rename FISTL -> FUNDS_CENTER and add the initial-value fields
    __target_df = (
        df.rename(columns={"FISTL": "FUNDS_CENTER"})
          .assign(UPD_DATE="00000000", GN_R3_SSY="")
          .reset_index(drop=True)
    )
    return __target_df
