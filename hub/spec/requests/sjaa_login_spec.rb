require "rails_helper"

RSpec.describe "Log in with SJAA", type: :request do
  let(:client) { instance_double(Sjaa::Client) }
  let(:person) do
    Sjaa::Person.new(id: 42, first_name: "Ada", last_name: "Lovelace", email: "ada@example.com", emails: [ "ada@example.com" ],
                     membership_ends: [ Date.current + 60 ], lifetime: false)
  end

  before do
    allow(Sjaa::Client).to receive_messages(configured?: true, new: client)
    allow(client).to receive(:find_person_by_email).and_return(nil)
    allow(client).to receive(:find_person_by_email).with("ada@example.com").and_return(person)
  end

  # Asks for a link and returns the path it emails.
  def request_link(email = "ada@example.com")
    perform_enqueued_jobs { post sjaa_login_path, params: { email: email } }
    expect(response).to redirect_to(sjaa_login_path)
    mail = ActionMailer::Base.deliveries.last
    URI(mail.text_part.body.to_s[%r{http://\S+}]).request_uri
  end

  def token_from(path)
    Rack::Utils.parse_query(URI(path).query)["token"]
  end

  it "offers the SJAA login on the sign-in page" do
    get new_user_session_path
    expect(response.body).to include("Log in with SJAA")
  end

  it "emails a link to the SJAA address and, once confirmed, creates a linked account and signs in" do
    link = request_link
    expect(ActionMailer::Base.deliveries.last.to).to eq([ "ada@example.com" ])

    get link
    expect(response).to have_http_status(:ok)
    expect(response.body).to include("ada@example.com")
    expect(User.count).to eq(0) # following the link alone does nothing

    post sjaa_login_verify_path, params: { token: token_from(link) }
    user = User.find_by!(email: "ada@example.com")
    expect(user).to have_attributes(name: "Ada Lovelace", sjaa_person_id: 42)
    expect(user).to be_sjaa_membership_current
    expect(response).to redirect_to(root_path)
    follow_redirect!
    expect(response.body).to include("Logged in with SJAA")
  end

  it "answers the same for addresses SJAA doesn't know, without emailing them" do
    expect { perform_enqueued_jobs { post sjaa_login_path, params: { email: "nobody@example.com" } } }
      .not_to change(ActionMailer::Base.deliveries, :count)
    expect(flash[:notice]).to include("If nobody@example.com is the address on an SJAA membership")
  end

  it "rejects tampered links and links used twice" do
    link = request_link
    post sjaa_login_verify_path, params: { token: "#{token_from(link)}x" }
    expect(flash[:alert]).to include("invalid or has expired")

    with_cache(ActiveSupport::Cache::MemoryStore.new) do
      post sjaa_login_verify_path, params: { token: token_from(link) }
      expect(response).to redirect_to(root_path)
      delete destroy_user_session_path
      post sjaa_login_verify_path, params: { token: token_from(link) }
      expect(flash[:alert]).to include("already been used")
    end
  end

  it "expires links" do
    link = request_link
    travel SjaaLoginsController::LINK_TTL + 1.minute do
      post sjaa_login_verify_path, params: { token: token_from(link) }
    end
    expect(flash[:alert]).to include("expired")
  end

  it "links a signed-in account whose email differs, and only in that account's session" do
    user = create(:user, email: "hub@example.com", name: "Hub Name")
    sign_in user
    link = request_link
    sign_out user

    other = create(:user)
    sign_in other
    post sjaa_login_verify_path, params: { token: token_from(link) }
    expect(response).to redirect_to(new_user_session_path)
    expect(other.reload).not_to be_sjaa_linked

    sign_in user
    post sjaa_login_verify_path, params: { token: token_from(link) }
    expect(response).to redirect_to(edit_profile_path)
    expect(user.reload).to have_attributes(sjaa_person_id: 42, name: "Ada Lovelace", email: "ada@example.com")
  end

  it "refreshes and unlinks from the profile, and locks the name while linked" do
    user = create(:user, email: "ada@example.com")
    user.link_sjaa!(person)
    sign_in user

    renewed = person.dup.tap { |p| p.membership_ends = [ Date.current + 400 ] }
    allow(client).to receive(:find_person_by_email).with("ada@example.com").and_return(renewed)
    post refresh_sjaa_profile_path
    expect(user.reload.sjaa_membership_expires_on).to eq(Date.current + 400)

    get edit_profile_path
    expect(response.body).to include("Linked", "Current until #{(Date.current + 400).to_fs(:long)}", "Unlink")

    patch profile_path, params: { user: { name: "Someone Else" } }
    expect(user.reload.name).to eq("Ada Lovelace")

    delete unlink_sjaa_profile_path
    expect(user.reload).not_to be_sjaa_linked
  end

  it "shows the SJAA panel on members' profiles but not admins'" do
    sign_in create(:user)
    get edit_profile_path
    expect(response.body).to include("SJAA membership", "Not linked")
    expect(Nokogiri::HTML(response.body).at_css("a[href='#{sjaa_login_path}']").text).to eq("Link SJAA membership")

    sign_in create(:user, :admin)
    get edit_profile_path
    expect(response.body).not_to include("SJAA membership")
  end

  it "says so when SJAA can't be reached" do
    allow(client).to receive(:find_person_by_email).and_raise(Sjaa::Error, "boom")
    post sjaa_login_path, params: { email: "ada@example.com" }
    expect(flash[:alert]).to include("couldn't reach")
  end

  it "is unavailable when no SJAA key is configured" do
    allow(Sjaa::Client).to receive(:configured?).and_return(false)
    get sjaa_login_path
    expect(response).to redirect_to(new_user_session_path)
    get new_user_session_path
    expect(response.body).not_to include("Log in with SJAA")

    sign_in create(:user)
    get edit_profile_path
    expect(response.body).not_to include("SJAA membership")
  end

  def with_cache(store)
    original = Rails.cache
    Rails.cache = store
    yield
  ensure
    Rails.cache = original
  end
end
