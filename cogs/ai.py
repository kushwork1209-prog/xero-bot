"""XERO Bot — AI Commands (15 commands) — Powered by NVIDIA Llama 4 Maverick"""
import discord
from utils.guard import command_guard
import asyncio
from discord.ext import commands
from discord import app_commands
import logging
from utils.embeds import comprehensive_embed, success_embed, error_embed, info_embed

logger = logging.getLogger("XERO.AI")

# Per-guild conversation memory (guild_id -> list of {role, content})
MEMORY: dict = {}


class AI(commands.GroupCog, name="ai"):
    def __init__(self, bot):
        self.bot = bot

    def _chunk(self, text: str, limit=4000) -> str:
        return text[:limit] + ("..." if len(text) > limit else "")

    async def _send(self, interaction: discord.Interaction, title: str, content: str, color=None):
        import discord as d
        embed = comprehensive_embed(title=title, description=self._chunk(content), color=color or d.Color.blurple(), footer_text="XERO AI | Powered by NVIDIA Llama 4 Maverick")
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed)
        else:
            await interaction.response.send_message(embed=embed)

    # ── Ask ───────────────────────────────────────────────────────────────
    @app_commands.command(name="ask", description="Ask the AI any question and get a detailed, intelligent answer.")
    @app_commands.describe(question="Your question for the AI", image_url="Optional image URL for image analysis")
    @command_guard
    async def ask(self, interaction: discord.Interaction, question: str, image_url: str = None):
        await interaction.response.defer()
        if image_url:
            response = await self.bot.nvidia.analyze_image_url(image_url, question)
            await self._send(interaction, "🖼️ AI Vision Analysis", response)
        else:
            response = await self.bot.nvidia.ask(question)
            await self._send(interaction, "🤖 AI Response", response)

    # ── Chat ──────────────────────────────────────────────────────────────
    @app_commands.command(name="chat", description="Have a context-aware conversation with the AI (remembers history).")
    @app_commands.describe(message="Your message to the AI")
    @command_guard
    async def chat(self, interaction: discord.Interaction, message: str):
        await interaction.response.defer()
        gid = interaction.guild.id
        if gid not in MEMORY:
            MEMORY[gid] = []
        settings = await self.bot.db.get_guild_settings(gid)
        persona = settings.get("persona", "neutral")
        response = await self.bot.nvidia.chat_with_context(MEMORY[gid], message, persona)
        MEMORY[gid].append({"role": "user", "content": message})
        MEMORY[gid].append({"role": "assistant", "content": response})
        if len(MEMORY[gid]) > 20:
            MEMORY[gid] = MEMORY[gid][-20:]
        await self._send(interaction, "💬 AI Chat", response)

    # ── Summarize ─────────────────────────────────────────────────────────
    @app_commands.command(name="summarize", description="Summarize any text or recent chat messages.")
    @app_commands.describe(text="Text to summarize (leave empty to summarize last 50 messages)", limit="Number of chat messages to summarize")
    @command_guard
    async def summarize(self, interaction: discord.Interaction, text: str = None, limit: int = 50):
        await interaction.response.defer()
        content = text
        if not content:
            messages = []
            async for msg in interaction.channel.history(limit=max(5, min(100, limit))):
                if not msg.author.bot and msg.content:
                    messages.append(f"{msg.author.display_name}: {msg.content}")
            if not messages:
                return await interaction.followup.send(embed=error_embed("No Content", "No messages found to summarize."), ephemeral=True)
            content = "\n".join(reversed(messages))
        response = await self.bot.nvidia.summarize(content)
        await self._send(interaction, "📝 AI Summary", response, discord.Color.teal())

    # ── Translate ─────────────────────────────────────────────────────────
    @app_commands.command(name="translate", description="Translate text to any language with high accuracy.")
    @app_commands.describe(text="Text to translate", language="Target language (e.g. Spanish, French, Japanese)")
    @command_guard
    async def translate(self, interaction: discord.Interaction, text: str, language: str = "Spanish"):
        await interaction.response.defer()
        response = await self.bot.nvidia.translate(text, language)
        embed = comprehensive_embed(title=f"🌐 Translation → {language}", description=response, color=discord.Color.blue())
        embed.add_field(name="Original", value=text[:500], inline=False)
        await interaction.followup.send(embed=embed)

    # ── Brainstorm ────────────────────────────────────────────────────────
    @app_commands.command(name="brainstorm", description="Generate creative ideas on any topic.")
    @app_commands.describe(topic="Topic to brainstorm about", count="Number of ideas to generate (max 15)")
    @command_guard
    async def brainstorm(self, interaction: discord.Interaction, topic: str, count: int = 10):
        await interaction.response.defer()
        count = max(3, min(15, count))
        response = await self.bot.nvidia.brainstorm(topic, count)
        await self._send(interaction, f"💡 Brainstorm: {topic[:40]}", response, discord.Color.gold())

    # ── Code Explain ──────────────────────────────────────────────────────
    @app_commands.command(name="code-explain", description="Get a detailed explanation of any code snippet.")
    @app_commands.describe(code="Code to explain")
    @command_guard
    async def code_explain(self, interaction: discord.Interaction, code: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.explain_code(code)
        await self._send(interaction, "💻 Code Explanation", response, discord.Color.green())

    # ── Code Debug ────────────────────────────────────────────────────────
    @app_commands.command(name="code-debug", description="Debug code and get a fixed version with explanations.")
    @app_commands.describe(code="Code to debug", error="Error message you're seeing (optional)")
    @command_guard
    async def code_debug(self, interaction: discord.Interaction, code: str, error: str = ""):
        await interaction.response.defer()
        response = await self.bot.nvidia.debug_code(code, error)
        await self._send(interaction, "🐛 Code Debug", response, discord.Color.red())

    # ── Sentiment ─────────────────────────────────────────────────────────
    @app_commands.command(name="sentiment", description="Deep sentiment and emotional analysis of any text.")
    @app_commands.describe(text="Text to analyze")
    @command_guard
    async def sentiment(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.analyze_sentiment(text)
        embed = comprehensive_embed(title="😊 Sentiment Analysis", description=response, color=discord.Color.blurple())
        embed.add_field(name="Analyzed Text", value=text[:500], inline=False)
        await interaction.followup.send(embed=embed)

    # ── Rewrite ───────────────────────────────────────────────────────────
    @app_commands.command(name="rewrite", description="Rewrite text in a different style (professional, casual, formal, etc.).")
    @app_commands.describe(text="Text to rewrite", style="Target style")
    @app_commands.choices(style=[
        app_commands.Choice(name="Professional", value="professional"),
        app_commands.Choice(name="Casual & Friendly", value="casual and friendly"),
        app_commands.Choice(name="Formal Academic", value="formal academic"),
        app_commands.Choice(name="Simple & Clear", value="simple and easy to understand"),
        app_commands.Choice(name="Persuasive", value="persuasive and compelling"),
        app_commands.Choice(name="Poetic", value="poetic and creative"),
    ])
    @command_guard
    async def rewrite(self, interaction: discord.Interaction, text: str, style: str = "professional"):
        await interaction.response.defer()
        response = await self.bot.nvidia.rewrite(text, style)
        embed = success_embed(f"Rewritten ({style})", response)
        embed.add_field(name="Original", value=text[:400], inline=False)
        await interaction.followup.send(embed=embed)

    # ── Grammar Check ─────────────────────────────────────────────────────
    @app_commands.command(name="grammar", description="Check grammar, spelling, and style with detailed corrections.")
    @app_commands.describe(text="Text to grammar-check")
    @command_guard
    async def grammar(self, interaction: discord.Interaction, text: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.check_grammar(text)
        await self._send(interaction, "✏️ Grammar Check", response, discord.Color.green())

    # ── Generate ──────────────────────────────────────────────────────────
    @app_commands.command(name="generate", description="Generate any type of content: stories, emails, scripts, posts, etc.")
    @app_commands.describe(prompt="Describe what you want generated in detail")
    @command_guard
    async def generate(self, interaction: discord.Interaction, prompt: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.generate(prompt, max_tokens=1500)
        await self._send(interaction, "📄 Generated Content", response, discord.Color.purple())

    # ── Fact Check ────────────────────────────────────────────────────────
    @app_commands.command(name="fact-check", description="AI fact-check any claim with detailed analysis and verdict.")
    @app_commands.describe(claim="The claim or statement to fact-check")
    @command_guard
    async def fact_check(self, interaction: discord.Interaction, claim: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.fact_check(claim)
        embed = comprehensive_embed(title="🔍 Fact Check", description=response, color=discord.Color.orange())
        embed.add_field(name="Claim", value=claim[:400], inline=False)
        await interaction.followup.send(embed=embed)

    # ── Roast ─────────────────────────────────────────────────────────────
    @app_commands.command(name="roast", description="Generate a funny, playful roast of anything (keep it lighthearted!).")
    @app_commands.describe(target="What or who to roast")
    @command_guard
    async def roast(self, interaction: discord.Interaction, target: str):
        await interaction.response.defer()
        response = await self.bot.nvidia.roast(target)
        await self._send(interaction, f"🔥 Roast: {target[:30]}", response, discord.Color.dark_orange())

    # ── Image Analyze ─────────────────────────────────────────────────────
    @app_commands.command(name="analyze-image", description="Analyze any image URL using NVIDIA Vision AI.")
    @app_commands.describe(image_url="Direct URL of the image to analyze", question="What do you want to know about the image?")
    @command_guard
    async def analyze_image(self, interaction: discord.Interaction, image_url: str, question: str = "Describe this image in comprehensive detail."):
        await interaction.response.defer()
        response = await self.bot.nvidia.analyze_image_url(image_url, question)
        embed = comprehensive_embed(title="🔭 AI Image Analysis", description=response, color=discord.Color.blurple())
        embed.set_image(url=image_url)
        await interaction.followup.send(embed=embed)

    # ── Clear Memory ──────────────────────────────────────────────────────
    @app_commands.command(name="clear-memory", description="Clear the AI's conversation memory for this server.")
    @app_commands.checks.has_permissions(manage_messages=True)
    async def clear_memory(self, interaction: discord.Interaction):
        gid = interaction.guild.id
        MEMORY.pop(gid, None)
        await interaction.response.send_message(embed=success_embed("Memory Cleared", "The AI's conversation history for this server has been reset."))


async def setup(bot):
    await bot.add_cog(AI(bot))
