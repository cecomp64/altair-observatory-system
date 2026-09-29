module Sjaa
  # A Person from the SJAA membership database, as GET /people returns it.
  # Membership mirrors SJAA's own rule (Person#is_active?): a membership with
  # no end date is a lifetime membership, otherwise it runs to its end.
  Person = Struct.new(:id, :first_name, :last_name, :email, :emails, :membership_ends, :lifetime, keyword_init: true) do
    def self.from_api(data)
      emails = [ data["email"], *Array(data["contacts"]).map { |c| c.is_a?(Hash) ? c["email"] : nil } ]
               .compact_blank.map { |e| e.to_s.strip.downcase }.uniq
      memberships = Array(data["memberships"]).select { |m| m.is_a?(Hash) }

      new(
        id: data["id"], first_name: data["first_name"].to_s.strip.presence, last_name: data["last_name"].to_s.strip.presence,
        email: emails.first, emails: emails,
        membership_ends: memberships.filter_map { |m| membership_end(m) },
        lifetime: memberships.any? { |m| m["end"].blank? && m["term_months"].blank? }
      )
    end

    # SJAA stores `end`; older records may only have start + term_months (it
    # ends at the end of the last month). Neither means a lifetime membership.
    def self.membership_end(membership)
      return parse_date(membership["end"]) if membership["end"].present?
      return nil if membership["term_months"].blank?

      start = parse_date(membership["start"])
      start && (start + membership["term_months"].to_i.months).end_of_month
    end

    def self.parse_date(value)
      Time.zone.parse(value.to_s)&.to_date
    rescue ArgumentError
      nil
    end

    def name
      [ first_name, last_name ].compact.join(" ").presence
    end

    # The last day of the latest membership; nil for lifetime members (and
    # for people who have never been members).
    def membership_expires_on
      lifetime ? nil : membership_ends.max
    end

    def active_member?(on = Date.current)
      lifetime || membership_ends.any? { |ends_on| ends_on >= on }
    end
  end
end
