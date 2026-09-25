require "json_schemer"

# Validates JSON against the shared API contract in ../contracts/schemas.
#
# Test-only: the Hub never reads contracts/ at runtime, so its Docker build
# context stays hub/ (docs/SYSTEM_ARCHITECTURE.md §3.6.2).
module ApiContract
  SCHEMAS_DIR = Rails.root.join("..", "contracts", "schemas").expand_path
  BASE_URI = "https://observatory.local/contracts/".freeze

  RESOLVER = lambda do |uri|
    relative = uri.to_s.delete_prefix(BASE_URI).sub(/#.*\z/, "")
    JSON.parse(SCHEMAS_DIR.join(relative).read)
  end

  def self.schema(name)
    @schemas ||= {}
    @schemas[name] ||= JSONSchemer.schema(
      SCHEMAS_DIR.join(name),
      ref_resolver: RESOLVER,
      format: true
    )
  end

  def self.errors(name, data)
    schema(name).validate(data).map { |error| error["error"] }
  end
end

RSpec::Matchers.define :match_api_contract do |schema_name|
  match do |data|
    data = JSON.parse(data.body) if data.respond_to?(:body)
    @errors = ApiContract.errors(schema_name, data)
    @errors.empty?
  end

  failure_message do
    "expected the payload to match contracts/schemas/#{schema_name}:\n  " + @errors.join("\n  ")
  end
end
