"""
Backward-compatible health module entry (tests / docs).

Production CMD: python -m sap_proxy_agent.hub
"""

from sap_proxy_agent.hub.health import app  # noqa: F401

if __name__ == "__main__":
    from sap_proxy_agent.hub.run import main

    main()
