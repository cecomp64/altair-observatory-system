class SjaaMailer < ApplicationMailer
  # The one-time link that proves a member reads the email SJAA has for them.
  def login_link(email:, name:, token:, linking: false)
    @name = name
    @linking = linking
    @url = sjaa_login_verify_url(token: token)
    @ttl = SjaaLoginsController::LINK_TTL

    mail to: email, subject: linking ? "Link your SJAA membership to the Remote Observatory" : "Your Remote Observatory login link"
  end
end
