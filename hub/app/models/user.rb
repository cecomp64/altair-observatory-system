class User < ApplicationRecord
  # Include default devise modules. Others available are:
  # :confirmable, :lockable, :timeoutable, :trackable and :omniauthable
  devise :database_authenticatable, :registerable,
         :recoverable, :rememberable, :validatable

  enum :role, { member: 0, admin: 1 }

  has_many :projects, dependent: :destroy
  has_many :targets, dependent: :destroy

  validates :name, presence: true
  validates :sjaa_person_id, uniqueness: true, allow_nil: true

  def admin?
    role == "admin"
  end

  # The Hub account for an SJAA Person: the one already linked to them, else
  # the one with their email, else a new account. Linked and saved.
  def self.find_or_create_from_sjaa!(person)
    user = find_by(sjaa_person_id: person.id) || find_by(email: person.email) ||
           new(password: Devise.friendly_token.first(32))
    user.link_sjaa!(person)
    user
  end

  # Links this account to an SJAA Person and mirrors their name, email and
  # membership. The email is left alone if another account already uses it.
  def link_sjaa!(person)
    self.sjaa_person_id = person.id
    self.sjaa_linked_at ||= Time.current
    self.name = person.name || name.presence || person.email.to_s.split("@").first
    if person.email.present? && !User.where.not(id: id).exists?(email: person.email)
      self.email = person.email
    end
    record_sjaa_membership(person)
    save!
  end

  def record_sjaa_membership(person)
    self.sjaa_membership_active = person.active_member?
    self.sjaa_membership_expires_on = person.membership_expires_on
    self.sjaa_membership_checked_at = Time.current
  end

  def unlink_sjaa!
    update!(sjaa_person_id: nil, sjaa_linked_at: nil, sjaa_membership_active: false,
            sjaa_membership_expires_on: nil, sjaa_membership_checked_at: nil)
  end

  def sjaa_linked?
    sjaa_person_id.present?
  end

  # A linked account whose membership was current when last checked and
  # hasn't run out since (a nil expiry is a lifetime membership).
  def sjaa_membership_current?(on = Date.current)
    sjaa_linked? && sjaa_membership_active? &&
      (sjaa_membership_expires_on.nil? || sjaa_membership_expires_on >= on)
  end

  def display_name
    name.presence || email
  end
end
