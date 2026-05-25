"""Self-service signup — free and paid (Asaas-backed) flows.

End users request an API key via ``POST /v1/signup/start`` (no auth).
``free`` plans get a key inline; paid plans go through an Asaas checkout
link and the key is generated on webhook confirmation.
"""
