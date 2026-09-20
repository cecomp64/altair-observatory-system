class UserMailer < ApplicationMailer
  def target_progress(target_event)
    @event = target_event
    @target = target_event.target
    @user = @target.user

    mail to: @user.email, subject: "#{@target.name}: #{@event.summary}"
  end
end
