class AdminMailer < ApplicationMailer
  def alert(admin, subject:, body:, url:)
    @body = body
    @url = url
    mail to: admin.email, subject: "[Observatory] #{subject}"
  end
end
