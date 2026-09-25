# Local development seed data.
#   bin/rails db:seed

admin = User.find_or_create_by!(email: "admin@example.com") do |u|
  u.name = "Observatory Admin"
  u.password = "password123"
  u.password_confirmation = "password123"
  u.role = :admin
end

member = User.find_or_create_by!(email: "member@example.com") do |u|
  u.name = "Jamie Member"
  u.password = "password123"
  u.password_confirmation = "password123"
  u.role = :member
  u.notify_email = true
end

telescope = Telescope.find_or_create_by!(slug: "backyard-16in") do |t|
  t.name = "Backyard 16\""
  t.latitude = 37.34
  t.longitude = -121.89
  t.elevation_m = 100
  t.description = "16-inch reflector at the club dark site."
  t.self_serve_submit = true
end

unless telescope.horizon_file.attached?
  horizon_csv = (0..350).step(10).map { |az| "#{az},#{15 + 10 * Math.sin(az * Math::PI / 180)}" }.join("\n")
  telescope.horizon_file.attach(
    io: StringIO.new(horizon_csv),
    filename: "#{telescope.slug}-horizon.csv",
    content_type: "text/csv"
  )
end

api_key = telescope.api_keys.find_or_initialize_by(name: "Local worker (dev)")
if api_key.new_record?
  token = api_key.generate_token!
  api_key.save!
  puts "Created dev API key for #{telescope.name}: #{token}"
end

target = Target.find_or_create_by!(user: member, telescope: telescope, name: "M42 - Orion Nebula") do |t|
  t.ra_deg = 83.822
  t.dec_deg = -5.391
  t.status = :in_progress
  t.priority = 1
  t.submitted_at = 2.days.ago
  t.notes = "Bright target, good for testing the wizard end to end."
end

if target.exposure_plans.empty?
  target.exposure_plans.create!(filter: "Luminance", exposure_seconds: 300, desired_count: 20, completed_count: 12)
  target.exposure_plans.create!(filter: "Ha", exposure_seconds: 600, desired_count: 10, completed_count: 3)
end

puts "Seeded #{User.count} users, #{Telescope.count} telescopes, #{Target.count} targets."
