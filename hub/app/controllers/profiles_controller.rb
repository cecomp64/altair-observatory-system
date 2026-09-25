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

  private

  def profile_params
    params.require(:user).permit(:name, :sjaa_membership_number, :discord_webhook_url, :notify_email, :notify_discord)
  end
end
