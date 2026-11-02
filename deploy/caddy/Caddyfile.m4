marketassistant.{$PILOT_HOST:test} {
	tls internal
	encode zstd gzip
	reverse_proxy web:80
}

api.marketassistant.{$PILOT_HOST:test} {
	tls internal
	encode zstd gzip
	reverse_proxy api:8000
}

auth.marketassistant.{$PILOT_HOST:test} {
	tls internal
	encode zstd gzip
	reverse_proxy keycloak:8080
}
