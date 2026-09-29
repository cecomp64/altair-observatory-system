class ProfilesController < ApplicationController
  def edit
    @user = current_user
  end

  def update
    if current_user.update(profile_params)
      redirect_to edit_profile_path, notice: "Profile updated."
    else
      @user = current_user
      render :edit, status: :unprocessable_content
    end
  end

  # Re-reads a linked membership from SJAA (after a renewal, say). SJAA is
  # searched by the account's email, which mirrors SJAA's.
  def refresh_sjaa
    return redirect_to(edit_profile_path, alert: "Log in with SJAA to link your membership first.") unless current_user.sjaa_linked?
    return redirect_to(edit_profile_path, alert: "SJAA isn't set up on this Hub.") unless Sjaa::Client.configured?

    person = Sjaa::Client.new.find_person_by_email(current_user.email)
    if person&.id == current_user.sjaa_person_id
      current_user.link_sjaa!(person)
      redirect_to edit_profile_path, notice: "SJAA membership refreshed."
    else
      redirect_to edit_profile_path, alert: "SJAA has no membership for #{current_user.email} any more. Log in with SJAA again to relink."
    end
  rescue Sjaa::Error => e
    Rails.logger.warn("[sjaa] refresh failed: #{e.message}")
    redirect_to edit_profile_path, alert: "We couldn't reach the SJAA membership database. Please try again later."
  end

  def unlink_sjaa
    current_user.unlink_sjaa!
    redirect_to edit_profile_path, notice: "Your account is no longer linked to SJAA."
  end

  private

  # A linked account's name comes from SJAA.
  def profile_params
    permitted = [ :discord_webhook_url, :notify_email, :notify_discord ]
    permitted << :name unless current_user.sjaa_linked?
    params.require(:user).permit(*permitted)
  end
end
