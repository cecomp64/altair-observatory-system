module Sjaa
  # Reads members from the SJAA membership database's REST API
  # (https://github.com/sjaa/sjaa-memberships/tree/main/docs/api-reference).
  #
  # That API only issues keys to SJAA admin accounts, and it can't check a
  # member's password, so the Hub uses one service key with `read`
  # permission (SJAA_API_TOKEN or credentials sjaa.api_token) and proves a
  # member's identity by emailing a sign-in link to the address SJAA has on
  # record (see SjaaLoginsController).
  class Client
    BASE_URL = ENV.fetch("SJAA_API_URL", "https://membership.sjaa.net/api")
    MAX_PAGES = 5

    def self.api_token
      ENV["SJAA_API_TOKEN"].presence || Rails.application.credentials.dig(:sjaa, :api_token)
    end

    def self.configured?
      api_token.present?
    end

    def initialize(api_token: self.class.api_token, connection: nil)
      @connection = connection || Faraday.new(url: BASE_URL, request: { timeout: 15 }) do |f|
        f.headers["Authorization"] = "Bearer #{api_token}"
        f.headers["Accept"] = "application/json"
      end
    end

    # The Person whose email is exactly this address, or nil. SJAA's email
    # filter is a substring match, so matches are compared here; two people
    # sharing an address is treated as no match. Raises Sjaa::Error when SJAA
    # can't be asked.
    def find_person_by_email(email)
      email = email.to_s.strip.downcase
      return nil if email.blank?

      matches = people(email: email).select { |person| person.emails.include?(email) }.uniq(&:id)
      matches.one? ? matches.first : nil
    end

    private

    def people(filter)
      results = []
      page = nil
      MAX_PAGES.times do
        body = get("people", page ? filter.merge(page: page) : filter)
        # GET /people returns {"people": [...], "next_page": n}; the OpenAPI
        # document describes a bare array, so accept that too.
        rows = body.is_a?(Hash) ? Array(body["people"]) : Array(body)
        results.concat(rows.map { |row| Person.from_api(row) })
        page = body.is_a?(Hash) ? body["next_page"] : nil
        break if page.blank?
      end
      results
    end

    def get(path, params)
      response = @connection.get(path, params)
      raise Error, "SJAA API HTTP #{response.status}" unless response.success?

      JSON.parse(response.body)
    rescue Faraday::Error, JSON::ParserError => e
      raise Error, "SJAA API: #{e.message}"
    end
  end
end
