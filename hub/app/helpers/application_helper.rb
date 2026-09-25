module ApplicationHelper
  include Pagy::Frontend

  def sjaa_membership_url
    ENV.fetch("SJAA_MEMBERSHIP_URL", "https://www.sjaa.net/membership/")
  end
end
