# "Log in with SJAA". SJAA's API can't check a member's password, so the
# member gives the email address SJAA has on record and we email a one-time
# link to it (see Sjaa::Client). Following the link finds the Hub account
# linked to that SJAA Person, or the one with that email, or creates one;
# mirrors the Person's name, email and membership into it; and signs in.
#
# Signed in, the same flow links the current account instead, so a member
# whose Hub email differs from SJAA's can link it.
class SjaaLoginsController < ApplicationController
  LINK_TTL = 30.minutes

  skip_before_action :authenticate_user!
  before_action :require_sjaa

  rate_limit to: 5, within: 10.minutes, only: :create,
             with: -> { redirect_to sjaa_login_path, alert: "Too many attempts. Try again in a few minutes." }

  def self.verifier
    Rails.application.message_verifier(:sjaa_login)
  end

  def new
  end

  def create
    email = params[:email].to_s.strip.downcase
    person = Sjaa::Client.new.find_person_by_email(email)
    if person
      token = self.class.verifier.generate(
        { "person_id" => person.id, "email" => email, "user_id" => current_user&.id, "nonce" => SecureRandom.hex(16) },
        expires_in: LINK_TTL, purpose: :sjaa_login
      )
      SjaaMailer.login_link(email: email, name: person.name, token: token, linking: user_signed_in?).deliver_later
    end
    # The same answer either way, so the form doesn't reveal who is a member.
    redirect_to sjaa_login_path, notice: "If #{email} is the address on an SJAA membership, we've emailed it a link " \
                                         "to log in. The link works once, for #{LINK_TTL.inspect}."
  rescue Sjaa::Error => e
    Rails.logger.warn("[sjaa] lookup failed: #{e.message}")
    redirect_to sjaa_login_path, alert: "We couldn't reach the SJAA membership database. Please try again later."
  end

  # The emailed link. Mail scanners follow links, so this only shows a button;
  # the POST uses up the link.
  def verify
    @data = read_token or return
    @token = params[:token]
  end

  def confirm
    data = read_token or return
    # A link to connect an account only works in that account's session, so
    # whoever holds the SJAA mailbox can't attach it to someone else's.
    if data["user_id"] && current_user&.id != data["user_id"]
      return redirect_to(new_user_session_path, alert: "Log in to the Hub account that asked for this link, then open it again.")
    end
    person = Sjaa::Client.new.find_person_by_email(data["email"])
    if person.nil? || person.id != data["person_id"]
      return redirect_to(sjaa_login_path, alert: "Your SJAA record has changed since we sent that link. Ask for a new one.")
    end
    unless Rails.cache.write("sjaa-login/#{data['nonce']}", true, expires_in: LINK_TTL, unless_exist: true)
      return redirect_to(sjaa_login_path, alert: "That link has already been used. Ask for a new one.")
    end

    if data["user_id"]
      if User.where.not(id: current_user.id).exists?(sjaa_person_id: person.id)
        return redirect_to(edit_profile_path, alert: "That SJAA membership is already linked to another Hub account.")
      end

      current_user.link_sjaa!(person)
      redirect_to edit_profile_path, notice: "Linked to SJAA. #{membership_notice(current_user)}"
    else
      user = User.find_or_create_from_sjaa!(person)
      sign_in(user)
      redirect_to after_sign_in_path_for(user), notice: "Logged in with SJAA. #{membership_notice(user)}"
    end
  rescue Sjaa::Error => e
    Rails.logger.warn("[sjaa] confirm failed: #{e.message}")
    redirect_to sjaa_login_path, alert: "We couldn't reach the SJAA membership database. Please try again later."
  rescue ActiveRecord::RecordInvalid => e
    redirect_to sjaa_login_path, alert: "We couldn't set up your account: #{e.record.errors.full_messages.to_sentence}."
  end

  private

  def require_sjaa
    return if Sjaa::Client.configured?

    redirect_to(user_signed_in? ? edit_profile_path : new_user_session_path, alert: "Logging in with SJAA isn't set up on this Hub.")
  end

  def read_token
    data = self.class.verifier.verified(params[:token].to_s, purpose: :sjaa_login)
    return data if data.is_a?(Hash)

    redirect_to sjaa_login_path, alert: "That link is invalid or has expired. Ask for a new one."
    nil
  end

  def membership_notice(user)
    if !user.sjaa_membership_current? then "Your SJAA membership isn't current."
    elsif user.sjaa_membership_expires_on then "Membership current until #{user.sjaa_membership_expires_on.to_fs(:long)}."
    else "Lifetime membership."
    end
  end
end
